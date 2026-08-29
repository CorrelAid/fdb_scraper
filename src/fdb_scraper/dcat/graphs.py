"""The two RDF documents this repository publishes: the dataset, and the vocabulary.

Both are built as whole graphs and serialised by
:mod:`fdb_scraper.dcat.artefacts`, so nothing here touches the filesystem and a
test can assert on triples rather than on parsed output.

No literal text is written here: every title, description, keyword, code and name
is declared in :mod:`fdb_scraper.config`, so a change to what is *said* about the
dataset does not mean reading the code that says it.
"""

from __future__ import annotations

from datetime import datetime

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, SKOS, XSD

from fdb_scraper.codelists import matches
from fdb_scraper.generated import CLOSED_VOCABS
from fdb_scraper.semantics import SCHEME_VOCAB, SCHEMES
from fdb_scraper.dcat.columns import (
    concept_uri,
    scheme_uri,
    inferred_note,
    meaning_note,
    range_of,
    vocab_note,
)
from fdb_scraper.config import (
    AUTHORITY_CODES,
    DATASET,
    DATASET_ID,
    DATASET_TITLE,
    DESCRIPTION,
    DISTRIBUTIONS,
    DOWNLOAD_BASE,
    EXPORT_URL,
    INFERRED_COLUMNS,
    KEYWORDS_DE,
    LANDING_PAGE,
    LICENCE_URI,
    LICENSOR,
    NAMESPACES,
    ONTOLOGY_DESCRIPTION_DE,
    ONTOLOGY_TITLE_DE,
    ORIGIN_COMMENT_EN,
    ORIGIN_TERM,
    PIVOTS,
    POLITICAL_LEVELS,
    PUBLISHER,
    RECORD_CLASS,
    RECORD_COMMENT_DE,
    RECORD_LABEL,
    SCHEMA_URL,
    SOURCE_HOMEPAGE,
    VOCAB,
)
from fdb_scraper.dcat.profile import (
    DCAT,
    DCATAP,
    DCATAPDE,
    DCT,
    FDB,
    FOAF,
    MEDIA_TYPE,
    OWL,
    POLITICAL_LEVEL,
    PROV,
    VCARD,
    authority,
)
from fdb_scraper.schema import ORIGIN, PUBLISHED_FIELDS
from fdb_scraper.semantics import PREDICATES, expand


def build_dataset(modified: datetime, sizes: dict[str, int]) -> Graph:
    """The harvestable document: one dataset, its distribution, publisher, contact.

    Everything a consumer cannot do without is in this one graph, because that is
    the only thing a harvester reads. Both piveau's ``importing-rdf`` and
    ckanext-dcat parse the document at the configured address and take the
    ``dcat:Dataset`` subjects they find in it; neither follows a link out to fetch
    the properties from somewhere else.

    No ``dcat:Catalog``: the aggregating catalogue adds its own node and its own
    ``dcat:dataset`` link to this dataset's URI when it merges this graph.

    ``sizes`` maps a distribution's filename to its byte count, for the files that
    have actually been built. A distribution with no entry gets no
    ``dcat:byteSize`` rather than a zero.
    """
    g = Graph()
    for prefix, uri in NAMESPACES.items():
        g.bind(prefix, Namespace(uri))
    g.bind("vcard", VCARD, override=True)

    dataset = URIRef(DATASET)
    correlaid = URIRef(PUBLISHER["uri"])
    # The contact point is this dataset's, not the organisation's, so it hangs
    # off the dataset URI: the fragment dereferences to the document that
    # describes it, which no agent URI minted under BASE would.
    contact = URIRef(f"{DATASET}#contact")

    # Name only. A harvester needs a label to show, so that much has to travel
    # in this document, but homepage and mailbox are stated authoritatively at
    # the IRI itself -- copied here they would be a second version to go stale.
    g.add((correlaid, RDF.type, FOAF.Agent))
    g.add((correlaid, FOAF.name, Literal(PUBLISHER["name"], lang="de")))

    # DCAT-AP requires a vcard:Kind. Organization is a subclass of it, but the
    # shapes check the asserted type, so state both rather than rely on a
    # validator having the vCard ontology loaded.
    g.add((contact, RDF.type, VCARD.Kind))
    g.add((contact, RDF.type, VCARD.Organization))
    g.add((contact, VCARD.fn, Literal(PUBLISHER["name"], lang="de")))
    g.add((contact, VCARD.hasEmail, URIRef(PUBLISHER["email"])))
    g.add((contact, VCARD.hasURL, URIRef(PUBLISHER["homepage"])))

    g.add((dataset, RDF.type, DCAT.Dataset))
    g.add((dataset, DCT.identifier, Literal(DATASET_ID)))
    for lang, title in DATASET_TITLE.items():
        g.add((dataset, DCT["title"], Literal(title, lang=lang)))
    # The field count is a consequence of the schema, so the template says
    # "{fields}" and it is filled here rather than typed into the prose.
    for lang, text in DESCRIPTION.items():
        g.add((
            dataset,
            DCT.description,
            Literal(text.format(fields=len(PUBLISHED_FIELDS)), lang=lang),
        ))
    g.add((dataset, DCT.publisher, correlaid))
    g.add((dataset, DCT.creator, correlaid))
    g.add((dataset, DCAT.contactPoint, contact))
    g.add((dataset, DCT.language, authority("language", AUTHORITY_CODES["language"])))
    g.add((
        dataset,
        DCT.accessRights,
        authority("access-right", AUTHORITY_CODES["access_right"]),
    ))
    g.add((
        dataset,
        DCT.accrualPeriodicity,
        authority("frequency", AUTHORITY_CODES["frequency"]),
    ))
    g.add((
        dataset,
        DCATAP.availability,
        authority("planned-availability", AUTHORITY_CODES["availability"]),
    ))
    g.add((dataset, DCT.modified, Literal(modified, datatype=XSD.dateTime)))
    for level in POLITICAL_LEVELS:
        g.add((dataset, DCATAPDE.politicalGeocodingLevelURI, URIRef(POLITICAL_LEVEL + level)))
    for theme in AUTHORITY_CODES["themes"]:
        g.add((dataset, DCAT.theme, authority("data-theme", theme)))
    for kw in KEYWORDS_DE:
        g.add((dataset, DCAT.keyword, Literal(kw, lang="de")))
    g.add((dataset, DCAT.landingPage, URIRef(LANDING_PAGE)))
    source_page = URIRef(SOURCE_HOMEPAGE)
    g.add((dataset, FOAF.page, source_page))
    g.add((source_page, RDF.type, FOAF.Document))
    g.add((dataset, PROV.wasDerivedFrom, URIRef(EXPORT_URL)))
    # The column-level contract. DCAT-AP.de has no vocabulary for it, so it
    # hangs off the dataset as a conformance target and off the CSV
    # distribution as its concrete schema.
    g.add((dataset, DCT.conformsTo, URIRef(SCHEMA_URL)))

    for spec in DISTRIBUTIONS:
        dist = URIRef(f"{DATASET}/distribution/{spec['slug']}")
        url = URIRef(DOWNLOAD_BASE + spec["file"])
        g.add((dataset, DCAT.distribution, dist))
        g.add((dist, RDF.type, DCAT.Distribution))
        g.add((dist, DCT["title"], Literal(spec["title_de"], lang="de")))
        g.add((dist, DCT.description, Literal(spec["desc_de"], lang="de")))
        g.add((dist, DCAT.accessURL, url))
        g.add((dist, DCAT.downloadURL, url))
        g.add((dist, DCT["format"], authority("file-type", spec["file_type"])))
        g.add((dist, DCAT.mediaType, URIRef(MEDIA_TYPE + spec["media_type"])))
        g.add((dist, DCT.license, URIRef(LICENCE_URI)))
        g.add((dist, DCATAPDE.licenseAttributionByText, Literal(LICENSOR, lang="de")))
        g.add((
            dist,
            DCATAP.availability,
            authority("planned-availability", AUTHORITY_CODES["availability"]),
        ))
        g.add((dist, DCT.modified, Literal(modified, datatype=XSD.dateTime)))
        if spec["conforms_to"]:
            g.add((dist, DCT.conformsTo, URIRef(spec["conforms_to"])))
        if (size := sizes.get(spec["file"])) is not None:
            g.add((dist, DCAT.byteSize, Literal(size, datatype=XSD.nonNegativeInteger)))

    return g


def build_vocabulary(modified: datetime) -> Graph:
    """The terms this dataset had to mint, as one dereferenceable document.

    :data:`fdb_scraper.config.VOCAB` is a hash namespace, so every term in it is a
    fragment of a single document -- one file makes the lot resolve, with no
    per-term hosting and no redirects.

    Only minted terms appear. The columns :data:`fdb_scraper.config.EXTERNAL`
    maps onto ``dct:``, ``foaf:`` or ``vcard:`` terms are described by whoever
    publishes those namespaces; restating them here would assert authority over
    someone else's vocabulary.

    The plain closed vocabularies are enumerated as skos concept schemes, so the
    labels the export gives its codes are published rather than left in the
    repository. A consumer reading ``arbeit`` in the CSV can resolve it to
    "Arbeit" here instead of cloning this project.

    The pivoted vocabularies are not. Their values are ``parent.child`` paths
    whose children come from one vocabulary per parent, so enumerating them means
    minting a concept per path and a hierarchy to go with it; the column comment
    states the shape instead, which is what :func:`vocab_note` produces.
    """
    g = Graph()
    for prefix, uri in NAMESPACES.items():
        g.bind(prefix, Namespace(uri))
    g.bind("owl", OWL)

    # The namespace is "...fdb#"; the document describing it is "...fdb".
    ontology = URIRef(VOCAB.rstrip("#"))
    g.add((ontology, RDF.type, OWL.Ontology))
    g.add((ontology, DCT["title"], Literal(ONTOLOGY_TITLE_DE, lang="de")))
    g.add((ontology, DCT.description, Literal(ONTOLOGY_DESCRIPTION_DE, lang="de")))
    g.add((ontology, DCT.publisher, URIRef(PUBLISHER["uri"])))
    g.add((ontology, DCT.modified, Literal(modified.date(), datatype=XSD.date)))
    g.add((ontology, DCT.source, URIRef(SOURCE_HOMEPAGE)))
    g.add((ontology, RDFS.seeAlso, URIRef(DATASET)))

    record = FDB[RECORD_CLASS]
    g.add((record, RDF.type, RDFS.Class))
    for lang, label in RECORD_LABEL.items():
        g.add((record, RDFS.label, Literal(label, lang=lang)))
    g.add((record, RDFS.comment, Literal(RECORD_COMMENT_DE, lang="de")))
    g.add((record, RDFS.isDefinedBy, ontology))

    # The one term that describes a column rather than holding a value of one. It
    # is what the CSVW schema annotates every column with, so it has to resolve in
    # this document like any other minted term.
    origin_term = URIRef(expand(ORIGIN_TERM))
    g.add((origin_term, RDF.type, RDF.Property))
    g.add((origin_term, RDFS.label, Literal("origin")))
    g.add((origin_term, RDFS.range, XSD.string))
    g.add((origin_term, RDFS.isDefinedBy, ontology))
    g.add((
        origin_term,
        RDFS.comment,
        Literal(
            ORIGIN_COMMENT_EN.format(inferred=", ".join(INFERRED_COLUMNS)), lang="en"
        ),
    ))

    for column in PUBLISHED_FIELDS:
        curie = PREDICATES[column]
        if not curie.startswith("fdb:"):
            continue  # a foreign term already says it
        term = URIRef(expand(curie))
        g.add((term, RDF.type, RDF.Property))
        g.add((term, RDFS.label, Literal(column)))
        g.add((term, RDFS.domain, record))
        g.add((term, RDFS.isDefinedBy, ontology))
        if (rng := range_of(column)) is not None:
            g.add((term, RDFS.range, rng))
        g.add((term, FDB.origin, Literal(ORIGIN[column])))
        # One comment per note rather than the joined string the CSVW schema
        # carries: RDF takes repeated properties, and the cell encoding a CSVW
        # consumer needs is not a fact about the property.
        for note in (meaning_note(column), vocab_note(column), inferred_note(column)):
            if note is not None:
                g.add((term, RDFS.comment, Literal(note, lang="en")))

    _add_concept_schemes(g, ontology)
    return g


def _add_concept_schemes(g: Graph, ontology: URIRef) -> None:
    """The closed vocabularies, so a code in the CSV resolves to its label.

    Only the plain ones. A pivoted column's values are ``parent.child`` paths
    drawn from a vocabulary per parent, which is a hierarchy rather than a flat
    scheme; :func:`fdb_scraper.dcat.columns.vocab_note` describes those in prose.

    The label is the export's own, so it is tagged ``de``: these are German
    categories from a German source, and translating them would be inventing.
    """
    for scheme in sorted(set(SCHEMES.values())):
        if scheme in PIVOTS:
            continue
        source = SCHEME_VOCAB[scheme]
        scheme_ref = URIRef(scheme_uri(scheme))
        g.add((scheme_ref, RDF.type, SKOS.ConceptScheme))
        g.add((scheme_ref, DCT["title"], Literal(source, lang="de")))
        g.add((scheme_ref, RDFS.isDefinedBy, ontology))
        for code, label in CLOSED_VOCABS[source].items():
            concept = URIRef(concept_uri(scheme, code))
            g.add((concept, RDF.type, SKOS.Concept))
            g.add((concept, SKOS.inScheme, scheme_ref))
            # The code as it appears in the cell, so a consumer can join on it.
            g.add((concept, SKOS.notation, Literal(code)))
            g.add((concept, SKOS.prefLabel, Literal(label, lang="de")))
        # topConceptOf on every concept: the scheme is flat, so they are all top.
        for code in CLOSED_VOCABS[source]:
            g.add((scheme_ref, SKOS.hasTopConcept, URIRef(concept_uri(scheme, code))))

    _add_codelist_matches(g)


def _add_codelist_matches(g: Graph) -> None:
    """Where a category corresponds to a code in a published codelist, say so.

    :mod:`fdb_scraper.codelists` aligns five of the vocabularies to XOEV and NUTS
    by label, and holds the relation it is willing to claim: ``exactMatch`` for
    the same concept, ``narrowMatch`` where theirs is narrower than ours. Those
    are skos properties already, so publishing them is a matter of emitting what
    the alignment table says rather than deciding anything here.

    A category with no counterpart contributes nothing. The alignment deliberately
    leaves those unmapped rather than folding them into a near miss, and the
    absence of a mapping is not a statement worth a triple -- ``unmatched()``
    carries the reasons for anyone who wants them.

    Only NUTS gives its codes dereferenceable URIs, so only those become a skos
    mapping property pointing at the code itself. The XOEV lists are identified by
    a URN and address a code only as a row of a Genericode document -- no URI to
    point at, and minting one on their behalf would be inventing an addressing
    scheme for someone else's vocabulary. Those mappings name the code in a
    comment and link the document it is defined in with ``rdfs:seeAlso``, so the
    target is reachable even though it is not addressable.

    The values stay the export's own codes; this only says what they line up with.
    """
    for row in matches().iter_rows(named=True):
        if row["relation"] is None:
            continue  # no counterpart; unmatched() says why
        concept = URIRef(concept_uri(SCHEMES[row["column"]], row["code"]))
        if row["codelist_uri"] is not None:
            g.add((concept, URIRef(expand(row["relation"])), URIRef(row["codelist_uri"])))
            continue
        g.add((
            concept,
            RDFS.comment,
            Literal(
                f'{row["relation"]} {row["codelist"]} '
                f'version {row["codelist_version"]} code {row["codelist_code"]} '
                f'("{row["codelist_label"]}") -- that codelist addresses its codes '
                "only as rows of its document, so there is no URI to point at",
                lang="en",
            ),
        ))
        if row["codelist_url"] is not None:
            g.add((concept, RDFS.seeAlso, URIRef(row["codelist_url"])))
