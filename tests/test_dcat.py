"""The published metadata has to satisfy the profile the harvester expects.

``dcat/id/dataset/foerderdatenbank-programme.ttl`` is the whole harvesting
interface: piveau's ``importing-rdf`` module fetches that one file, and the
Datenatlas instance runs the DCAT-AP.de profile (its records carry ``dcatapde:``
and ``dcatap:`` terms and EU authority vocabularies throughout). So the file is
validated here against the official DCAT-AP.de 3.0 SHACL shapes rather than
eyeballed -- 3.0 because that is what GovData's validator now runs and what the
aggregating catalogue gates its merge with, and a document that passes here has to
pass there too.

No ``dcat:Catalog`` is published from this repository, so no catalogue shape has a
focus node here. The catalogue that lists this dataset is a separate deployment
that fetches this document; what this file has to guarantee in its place is that
the document stands alone -- see
:func:`test_the_dataset_document_is_self_contained`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from pyshacl import validate
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, OWL, RDF, RDFS, SH, SKOS

from fdb_scraper.schema import PUBLISHED_FIELDS
from fdb_scraper.config import PIVOTS, VOCAB
from fdb_scraper.codelists import matches
from fdb_scraper.dcat.columns import concept_uri, scheme_uri, vocab_note
from fdb_scraper.generated import CLOSED_VOCABS
from fdb_scraper.semantics import ANNOTATIONS, PREDICATES, SCHEME_VOCAB, SCHEMES, expand

ROOT = Path(__file__).parent.parent
TABLE_SCHEMA = ROOT / "dcat" / "table-schema.json"
VOCABULARY = ROOT / "dcat" / "def" / "fdb.ttl"
DATASET_DOC = ROOT / "dcat" / "id" / "dataset" / "foerderdatenbank-programme.ttl"
# Namespace the project mints its own identifiers under. Used to tell a shape
# result about our own graph from one about a remote vocabulary we did not load.
#
# Written out rather than imported from fdb_scraper.config. These are published
# identifiers: importing them would make the assertions restate whatever the
# constant happens to say, and a changed host would pass silently. Spelled here,
# moving the host fails these tests until someone confirms the move on purpose.
OWN = "https://fdb.cdl.correlaid.org/id/"
DATASET = f"{OWN}dataset/foerderdatenbank-programme"
SHAPES_DIR = Path(__file__).parent / "fixtures" / "shapes"

# The "DCAT-AP.de 3.0 - Spezifikation" profile, which is two upstreams: SEMIC's
# DCAT-AP 3.0 shapes plus the German files that translate, restrict and extend
# them. Both halves are load-bearing -- dcat-ap-SHACL-DE.ttl deactivates and adds
# shapes rather than restating the base, so the German files alone pass an empty
# dcat:Dataset. tests/fixtures/shapes/README.md says which file does what.
#
# dcat-ap-de-imports.ttl is left out: no shapes of its own, only owl:imports of
# remote controlled vocabularies. Skipping it keeps the test offline, at the cost
# of not checking that each authority URI really is a concept in its vocabulary --
# run the file through https://www.itb.ec.europa.eu/shacl/dcat-ap.de/upload before
# publishing for that.
SHAPE_FILES = (
    "dcat-ap-SHACL.ttl",
    "dcat-ap-SHACL-DE.ttl",
    "dcat-ap-de-controlledvocabularies.ttl",
    "dcat-ap-spec-german-additions.ttl",
    "dcat-ap-de-deprecated.ttl",
)


@pytest.fixture(scope="module")
def shapes() -> Graph:
    g = Graph()
    for name in SHAPE_FILES:
        g.parse(SHAPES_DIR / name, format="turtle")
    return g


@pytest.fixture(scope="module")
def published() -> Graph:
    return Graph().parse(DATASET_DOC, format="turtle")


def test_published_metadata_conforms_to_dcat_ap_de(published: Graph, shapes: Graph) -> None:
    """Every violation the shapes can decide without the remote vocabularies.

    Results on ``skos:inScheme`` are excluded, and only for focus nodes outside
    :data:`OWN`. Those shapes ask "is this IRI a concept in the vocabulary it
    comes from", which cannot be answered from a graph that does not contain the
    vocabulary, so offline they fail for every authority IRI regardless of
    whether it is right. They are a check on the vocabularies, not on this
    generator. The ITB validator resolves the imports and does decide them.

    The namespace condition keeps that exemption off our own terms: anything this
    project mints ships in the same graph, so the shape would be decidable and
    its result real. It exists so that adding one of our own documents to what is
    validated cannot silently exempt the part we are responsible for.
    """
    _, results, text = validate(
        published,
        shacl_graph=shapes,
        advanced=True,
        # No inference: the shapes are written against the asserted triples, and
        # RDFS closure over an incomplete import set only invents new failures.
        inference="none",
    )
    undecidable = 0
    violations = []
    for result in results.subjects(SH.resultSeverity, SH.Violation):
        focus = results.value(result, SH.focusNode)
        if results.value(result, SH.resultPath) == SKOS.inScheme and not str(
            focus
        ).startswith(OWN):
            undecidable += 1
            continue
        violations.append(
            f"{results.value(result, SH.focusNode)} "
            f"{results.value(result, SH.resultPath)}: "
            f"{results.value(result, SH.resultMessage)}"
        )
    assert not violations, "\n".join(violations) + f"\n\nfull report:\n{text}"
    # Fails loudly if an upstream shapes update stops producing them, which
    # would mean the exclusion above is now hiding something real.
    assert undecidable, "no skos:inScheme results -- is the exclusion still needed?"


def test_predicates_are_distinct() -> None:
    """Two columns sharing a predicate would silently merge in any RDF output."""
    seen: dict[str, str] = {}
    clashes = []
    for column, curie in PREDICATES.items():
        if (other := seen.setdefault(curie, column)) != column:
            clashes.append(f"{other} and {column} both map to {curie}")
    assert not clashes, clashes


def test_table_schema_covers_every_published_column() -> None:
    schema = json.loads(TABLE_SCHEMA.read_text())
    columns = schema["tableSchema"]["columns"]
    assert [c["name"] for c in columns] == list(PUBLISHED_FIELDS)
    assert all(c["propertyUrl"] for c in columns)


def test_every_code_of_a_plain_vocabulary_resolves_to_its_label() -> None:
    """The column comment points at a concept scheme; it has to be there.

    ``vocab_note`` used to name a Python module path, which is nothing to a
    consumer who has the CSV and not the repository. It now names a scheme URI in
    this document, so every code the data can hold must be a concept in it,
    carrying the label and the notation to join on.
    """
    g = Graph().parse(VOCABULARY, format="turtle")
    schemes = set(g.subjects(RDF.type, SKOS.ConceptScheme))
    assert schemes, "no concept schemes at all -- was the vocabulary regenerated?"

    for column, scheme in SCHEMES.items():
        if column in PIVOTS:
            continue  # a hierarchy, described in prose rather than enumerated
        scheme_ref = URIRef(scheme_uri(scheme))
        assert scheme_ref in schemes, f"{column} points at a missing scheme"
        assert scheme_uri(scheme) in vocab_note(column), (
            f"{column}'s comment does not name the scheme a consumer should fetch"
        )
        for code, label in CLOSED_VOCABS[SCHEME_VOCAB[scheme]].items():
            concept = URIRef(concept_uri(scheme, code))
            assert (concept, SKOS.inScheme, scheme_ref) in g, f"{code} not in {scheme}"
            assert (concept, SKOS.prefLabel, Literal(label, lang="de")) in g, (
                f"{code} carries no label"
            )
            assert (concept, SKOS.notation, Literal(code)) in g, (
                f"{code} has no notation to join the CSV on"
            )


def test_every_minted_term_has_an_anchor_on_the_html_page() -> None:
    """A hash namespace whose page has no anchors resolves every term to its top.

    ``fdb:`` is a hash namespace, so a term URI is the vocabulary page plus a
    fragment. The page carried no ``id`` attributes at all, which made every
    minted identifier -- 106 of them -- land at the top of the document.
    """
    page = (VOCABULARY.parent / "fdb.html").read_text(encoding="utf-8")
    anchors = set(re.findall(r'id="([^"]+)"', page))
    graph = Graph().parse(VOCABULARY, format="turtle")

    minted = {
        str(s).rsplit("#", 1)[-1]
        for kind in (RDF.Property, RDFS.Class, SKOS.ConceptScheme, SKOS.Concept)
        for s in graph.subjects(RDF.type, kind)
        if "#" in str(s)
    }
    assert minted, "no minted terms -- was the vocabulary regenerated?"
    assert minted - anchors == set(), f"no anchor for: {sorted(minted - anchors)}"


def test_every_codelist_alignment_is_published() -> None:
    """The alignment was reachable only from the repository, like the labels were.

    Where a code has a dereferenceable counterpart -- NUTS does -- it becomes the
    skos mapping property the alignment claims. The XOEV lists publish no URI per
    code, so those are stated as a comment rather than pointed at a URI we would
    have had to invent; both forms are asserted here so neither can be dropped
    silently.
    """
    g = Graph().parse(VOCABULARY, format="turtle")
    aligned = matches().filter(pl.col("relation").is_not_null())
    assert aligned.height, "no alignments at all -- did LINKED lose its entries?"

    for row in aligned.iter_rows(named=True):
        concept = URIRef(concept_uri(SCHEMES[row["column"]], row["code"]))
        if row["codelist_uri"] is not None:
            triple = (concept, URIRef(expand(row["relation"])), URIRef(row["codelist_uri"]))
            assert triple in g, f"{row['code']} lost its {row['relation']}"
        else:
            comments = " ".join(str(o) for o in g.objects(concept, RDFS.comment))
            assert row["codelist_code"] in comments, (
                f"{row['code']} does not name its {row['codelist']} counterpart"
            )
            # Not addressable is not the same as not reachable: the document the
            # code is defined in has a URL, and the mapping links it.
            assert URIRef(row["codelist_url"]) in set(g.objects(concept, RDFS.seeAlso)), (
                f"{row['code']} does not link the document its counterpart is in"
            )

    # A category the alignment leaves unmapped must not gain a mapping by accident.
    for row in matches().filter(pl.col("relation").is_null()).iter_rows(named=True):
        concept = URIRef(concept_uri(SCHEMES[row["column"]], row["code"]))
        assert not list(g.objects(concept, SKOS.exactMatch)), (
            f"{row['code']} has no counterpart but claims an exactMatch"
        )


def test_a_pivoted_vocabulary_is_described_rather_than_enumerated() -> None:
    """The deliberate asymmetry, so dropping it is a choice and not a drift."""
    g = Graph().parse(VOCABULARY, format="turtle")
    concepts = {str(c) for c in g.subjects(RDF.type, SKOS.Concept)}

    for column in PIVOTS:
        note = vocab_note(column)
        assert "parent" in note, f"{column} should describe its path shape"
        assert not any(f"#{column}-" in c for c in concepts), (
            f"{column} was enumerated; the note still says it is not"
        )


def test_every_minted_term_is_defined() -> None:
    """A minted term with no definition is a dead link in published metadata.

    The vocabulary document is the only thing standing behind the ``fdb:``
    namespace, so a new published column that gets a minted predicate has to
    arrive here too -- which it does automatically, unless someone stops
    regenerating.
    """
    g = Graph().parse(VOCABULARY, format="turtle")
    defined = {str(s) for s in g.subjects(RDF.type, RDF.Property)}
    # The column predicates, plus the terms that describe a column rather than hold
    # a value of one -- fdb:origin, which the CSVW schema puts on every column.
    minted = {
        expand(curie) for curie in PREDICATES.values() if curie.startswith("fdb:")
    } | {expand(curie) for curie in ANNOTATIONS}
    assert minted, "no minted terms at all -- has EXTERNAL swallowed every column?"
    assert minted - defined == set(), f"undefined: {sorted(minted - defined)}"
    # The reverse too: a term left behind by a renamed column would resolve to a
    # definition of something no longer published.
    assert defined - minted == set(), f"stale: {sorted(defined - minted)}"


def test_every_minted_term_resolves_within_the_vocabulary_document() -> None:
    """The namespace is a hash namespace, so one document must cover all of it."""
    doc = VOCAB.rstrip("#")
    g = Graph().parse(VOCABULARY, format="turtle")
    for term in g.subjects(RDF.type, RDF.Property):
        assert str(term).startswith(VOCAB), f"{term} is outside {VOCAB}"
        assert str(term).split("#")[0] == doc, f"{term} does not resolve to {doc}"


def test_the_dataset_document_is_self_contained(published: Graph) -> None:
    """One fetch has to yield the whole description, because that is all a harvester does.

    piveau's ``importing-rdf`` and ckanext-dcat both parse the document at the
    configured address and take the ``dcat:Dataset`` subjects out of that graph.
    Neither dereferences a URI to collect properties from a second document. So a
    node this dataset points at with a structural property has to be described in
    the same file, or the aggregating catalogue merges a dataset with a dangling
    publisher and the portal shows a blank field.

    Only our own identifiers are required to be described: the EU authority IRIs
    and the licence URIs are somebody else's to publish, and restating them here
    would assert authority over another namespace.
    """
    dataset = URIRef(DATASET)
    assert (dataset, RDF.type, DCAT.Dataset) in published, "the dataset describes itself"

    structural = (
        DCAT.distribution,
        DCAT.contactPoint,
        DCTERMS.publisher,
        DCTERMS.creator,
    )
    dangling = [
        f"{s} {p} {o}"
        for p in structural
        for s, o in published.subject_objects(p)
        if str(o).startswith(OWN) and (o, RDF.type, None) not in published
    ]
    assert not dangling, "referenced but not described:\n" + "\n".join(dangling)

    # A distribution the portal cannot download is a catalogue entry with no data
    # behind it, which is the one failure a consumer notices immediately.
    for dist in published.objects(dataset, DCAT.distribution):
        assert (dist, DCAT.downloadURL, None) in published, f"{dist} has no downloadURL"


def test_the_lab_is_identified_by_the_uri_its_own_website_publishes(
    published: Graph,
) -> None:
    """One identifier for the organisation, minted where the organisation is described.

    https://civic-data.de/#organization is the schema.org node the Civic Data Lab
    website serves. An agent URI minted under this deployment would be a second
    identifier for the same body, and one this project would then have to serve a
    document for -- a harvester merging two of our datasets would see two
    publishers. So nothing under ``OWN`` may be an agent.
    """
    organization = URIRef("https://civic-data.de/#organization")
    for predicate in (DCTERMS.publisher, DCTERMS.creator):
        objects = set(published.objects(URIRef(DATASET), predicate))
        assert objects == {organization}, f"{predicate}: {objects}"
    vocabulary = Graph().parse(VOCABULARY, format="turtle")
    assert set(vocabulary.objects(None, DCTERMS.publisher)) == {organization}

    minted_agents = [
        s for s in published.subjects(RDF.type, FOAF.Agent) if str(s).startswith(OWN)
    ]
    assert not minted_agents, f"an agent minted here: {minted_agents}"

    # A harvester does not dereference, so the label has to travel in this
    # document, and the Wikidata item with it: a catalogue that knows the
    # organisation by Q136186131 can only join the two if the link is stated.
    # Everything else about the organisation lives at the IRI, and a copy here
    # would be a second version of it to go stale.
    said = set(published.predicate_objects(organization))
    assert said == {
        (RDF.type, FOAF.Agent),
        (FOAF.name, Literal("Civic Data Lab", lang="de")),
        (OWL.sameAs, URIRef("http://www.wikidata.org/entity/Q136186131")),
    }, f"more than an identity is restated about the organisation: {said}"


def test_no_catalogue_is_published(published: Graph) -> None:
    """The catalogue is a separate deployment's to publish, not this one's.

    Two ``dcat:Catalog`` nodes claiming to be the Civic Data Lab's catalogue --
    one here, one in the aggregator -- is how a portal ends up harvesting the same
    dataset twice under two parents. The aggregator adds the catalogue node and
    the ``dcat:dataset`` link when it merges this graph.
    """
    catalogues = list(published.subjects(RDF.type, DCAT.Catalog))
    assert not catalogues, f"a catalogue crept back in: {catalogues}"
    assert not list(published.objects(None, DCAT.dataset)), (
        "dcat:dataset belongs to the aggregating catalogue"
    )


def test_generated_artefacts_are_up_to_date() -> None:
    """Regenerating must not change the committed files.

    Only ``dct:modified`` and ``dcat:byteSize`` are allowed to vary between
    runs, so the committed date is passed back in; anything else that moved is a
    file someone edited by hand instead of regenerating.
    """
    doc = Graph().parse(DATASET_DOC, format="turtle")
    modified = doc.value(URIRef(DATASET), DCTERMS.modified).toPython().date().isoformat()
    before = {p: p.read_bytes() for p in (TABLE_SCHEMA, VOCABULARY, DATASET_DOC)}
    subprocess.run(
        [sys.executable, "scripts/gen_dcat.py", "--modified", modified],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    try:
        for path, content in before.items():
            assert path.read_bytes() == content, f"{path.name} is stale, regenerate it"
    finally:
        for path, content in before.items():
            path.write_bytes(content)
