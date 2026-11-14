"""Deterministic synthetic inputs for the Go Police parity fixtures."""

import base64
import random

import httpx

from telescreen.collectors.google.adapter import GoogleAdapter, _line_segments, _matches
from telescreen.collectors.google.admin import AdminApi
from telescreen.collectors.google.drive import DriveFile, DrivePermission
from telescreen.collectors.google.exposure import drive_exposure
from telescreen.collectors.google.sweep import GoogleSweep, _included, _shard
from telescreen.core.json import (
    JsonValue,
    json_array,
    json_bool,
    json_float,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import CollectorKind, RuleClass
from telescreen.detection import RuleDetector
from telescreen.detection.types import DetectionResult, RawFinding, ScanItem
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import DriveSelector, Exposure, ResourceContent
from telescreen.ports.findings import MaskedFinding
from telescreen.ports.sweeps import PartitionPage, PartitionRequest, ShardSpec
from telescreen_police_sdk.masking import external_fingerprint, item_fingerprint
from tests.collectors.google.support import (
    GoogleTestBundle,
    SweepClientFactory,
    adapters_for,
    drive_config,
    make_bundle,
)
from tests.support.samples import all_samples


def generated_detector_cases() -> tuple[str, ...]:
    """Build the reproducible differential detector corpus."""
    rng: random.Random = random.Random(20261030)
    fragments: tuple[str, ...] = (
        *(sample.value for sample in all_samples()),
        "aws_secret",
        "secret_access_key",
        "password=",
        "1234567890",
        "１２３４５６７８９０",
        "١٢٣٤٥٦٧٨٩٠",
        "éİıKßﬃ",
        "α東京",
        "-_:/.@",
        " ",
        "\t",
        "\n",
        "\r\n",
        "550e8400-e29b-41d4-a716-446655440000",
    )
    return tuple(
        "".join(rng.choice(fragments) for _ in range(rng.randint(1, 6))) for _ in range(2000)
    )


def _b64(value: str) -> str:
    key: bytes = b"telescreen-go-parity-fixture"
    encoded: bytes = bytes(
        byte ^ key[index % len(key)] for index, byte in enumerate(value.encode())
    )
    return base64.b64encode(encoded).decode("ascii")


def _optional(value: str | None) -> JsonValue:
    return json_null() if value is None else json_str(value)


def _permission(
    kind: str,
    role: str,
    domain: str | None = None,
    email_address: str | None = None,
    allow_file_discovery: bool = False,
) -> DrivePermission:
    return DrivePermission(kind, role, domain, email_address, allow_file_discovery)


def exposure_cases() -> JsonValue:
    """Render classifications from synthetic Drive permissions."""
    inputs: tuple[tuple[str, tuple[DrivePermission, ...], str | None, tuple[str, ...]], ...] = (
        ("public", (_permission("anyone", "reader", allow_file_discovery=True),), None, ()),
        ("link", (_permission("anyone", "reader"),), None, ()),
        (
            "owner",
            (_permission("user", "owner", email_address="a@example.org"),),
            None,
            ("example.com",),
        ),
        (
            "external_user",
            (_permission("user", "reader", email_address="a@example.org"),),
            None,
            ("example.com",),
        ),
        (
            "external_group",
            (_permission("group", "reader", email_address="a@example.org"),),
            None,
            ("example.com",),
        ),
        (
            "internal_user",
            (_permission("user", "reader", email_address="a@EXAMPLE.COM"),),
            None,
            ("example.com",),
        ),
        (
            "domain",
            (_permission("domain", "reader", domain="EXAMPLE.COM"),),
            None,
            ("example.com",),
        ),
        (
            "external_domain",
            (_permission("domain", "reader", domain="example.org"),),
            None,
            ("example.com",),
        ),
        ("unknown", (_permission("user", "reader", email_address="a@example.org"),), None, ()),
        (
            "casefold",
            (_permission("domain", "reader", domain="strasse.example"),),
            None,
            ("straße.example",),
        ),
        (
            "malformed",
            (_permission("user", "reader", email_address="invalid"),),
            None,
            ("example.com",),
        ),
        ("shared", (), "drive-id", ("example.com",)),
    )
    cases: list[JsonValue] = []
    for name, permissions, drive_id, domains in inputs:
        result: Exposure = drive_exposure(permissions, drive_id, domains)
        grants: list[JsonValue] = [
            json_object(
                (
                    ("type", json_str(p.type)),
                    ("role", json_str(p.role)),
                    ("domain", _optional(p.domain)),
                    ("email_address", _optional(p.email_address)),
                    ("allow_file_discovery", json_bool(p.allow_file_discovery)),
                )
            )
            for p in permissions
        ]
        cases.append(
            json_object(
                (
                    ("name", json_str(name)),
                    ("permissions", json_array(grants)),
                    ("drive_id", _optional(drive_id)),
                    ("internal_domains", json_array([json_str(d) for d in domains])),
                    (
                        "expected",
                        json_object(
                            (
                                ("visibility", json_str(result.visibility.value)),
                                (
                                    "external_principal_count",
                                    json_int(result.external_principal_count),
                                ),
                                ("shared_drive", json_bool(result.shared_drive)),
                            )
                        ),
                    ),
                )
            )
        )
    return json_array(cases)


def _selector_json(selector: DriveSelector) -> JsonValue:
    return json_object(
        (
            ("drive_ids", json_array([json_str(x) for x in selector.drive_ids])),
            ("folder_ids", json_array([json_str(x) for x in selector.folder_ids])),
            ("mime_types", json_array([json_str(x) for x in selector.mime_types])),
        )
    )


def matching_cases() -> JsonValue:
    """Render MIME and corpus selection decisions from the reference."""
    mimes: tuple[str, ...] = (
        "text/plain",
        "application/json",
        "application/csv",
        "application/yaml",
        "application/xml",
        "application/example+xml",
        "application/example+json",
        "image/png",
        "application/pdf",
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
    )
    selectors: tuple[tuple[DriveSelector, str, str | None, tuple[str, ...]], ...] = (
        (DriveSelector((), (), ()), "user:fixture", None, ()),
        (DriveSelector(("d1",), ("f1", "f2"), ()), "drive:d1", "d1", ("f1",)),
        (DriveSelector(("d1",), ("f1", "f2"), ()), "folder:f2", None, ("f1", "f2")),
        (DriveSelector((), (), ("application/pdf",)), "user:fixture", None, ()),
    )
    cases: list[JsonValue] = []
    for kind in (CollectorKind.GDRIVE, CollectorKind.GDOCS, CollectorKind.GSHEETS):
        for selector, corpus, drive_id, parents in selectors:
            for mime in mimes:
                file: DriveFile = DriveFile(
                    "fixture", mime_type=mime, drive_id=drive_id, parents=parents
                )
                cases.append(
                    json_object(
                        (
                            ("kind", json_str(kind.value)),
                            ("selector", _selector_json(selector)),
                            ("corpus_key", json_str(corpus)),
                            (
                                "file",
                                json_object(
                                    (
                                        ("id", json_str(file.id)),
                                        ("mime_type", json_str(mime)),
                                        ("trashed", json_bool(file.trashed)),
                                        ("drive_id", _optional(drive_id)),
                                        ("parents", json_array([json_str(p) for p in parents])),
                                    )
                                ),
                            ),
                            ("matches", json_bool(_matches(file, selector, kind))),
                            ("included", json_bool(_included(file, corpus, selector))),
                        )
                    )
                )
    return json_array(cases)


def line_segment_cases() -> JsonValue:
    """Render line segments for Python splitlines boundaries."""
    texts: tuple[str, ...] = (
        "",
        "\ufefffirst\nsecond",
        "last\n",
        "a\r\nb",
        *("a" + separator + "b" for separator in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"),
    )
    return json_array(
        [
            json_object(
                (
                    ("text_b64", json_str(_b64(text))),
                    (
                        "segments",
                        json_array(
                            [
                                json_object(
                                    (
                                        ("locator", json_str(segment.locator)),
                                        ("text_b64", json_str(_b64(segment.text))),
                                    )
                                )
                                for segment in _line_segments(text)
                            ]
                        ),
                    ),
                )
            )
            for text in texts
        ]
    )


async def locator_cases() -> JsonValue:
    """Capture references, shards and partition requests through offline clients."""

    def respond(request: httpx.Request) -> httpx.Response:
        if "/admin/directory/" in request.url.path:
            return httpx.Response(200, json={"id": "u1", "primaryEmail": "user@example.invalid"})
        return httpx.Response(200, json={"files": []})

    bundle: GoogleTestBundle = make_bundle(respond)
    try:
        adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = adapters_for(bundle)
        factory: SweepClientFactory = SweepClientFactory(bundle.drive, adapters)
        admin: AdminApi = AdminApi(bundle.client, bundle.auth, "admin@example.invalid")
        refs: list[JsonValue] = []
        for adapter in adapters:
            for file_id in ("file-1", "quote'\\id"):
                refs.append(
                    json_object(
                        (
                            ("kind", json_str(adapter.kind.value)),
                            ("file_id", json_str(file_id)),
                            ("locator", json_str(adapter._ref(file_id).raw_locator)),
                        )
                    )
                )
        shards: list[JsonValue] = []
        for corpus, ids in (("drive:d1", ("z", "a", "m")), ("folder:f1", ("one",))):
            shard: ShardSpec = _shard(corpus, ids)
            shards.append(
                json_object(
                    (
                        ("corpus_key", json_str(corpus)),
                        ("file_ids", json_array([json_str(i) for i in ids])),
                        ("shard_key", json_str(shard.shard_key)),
                        ("descriptor", shard.descriptor),
                        ("weight", json_int(shard.weight)),
                    )
                )
            )
        queries: list[JsonValue] = []
        selectors: tuple[DriveSelector, ...] = (
            DriveSelector(("d1",), ("f'\\1",), ()),
            DriveSelector((), (), ("text/plain", "application/json")),
        )
        for kind in (CollectorKind.GDRIVE, CollectorKind.GDOCS, CollectorKind.GSHEETS):
            for selector in selectors:
                sweep: GoogleSweep = GoogleSweep(kind, factory, admin, 10)
                for corpus in ("user:u1", "drive:d1", "folder:f'\\1"):
                    cursor: str | None = "page-1" if corpus.startswith("folder:") else None
                    await sweep.partition(
                        drive_config(kind, selector), PartitionRequest(corpus, cursor, selector)
                    )
                    request: httpx.Request = bundle.api_requests[-1]
                    params: httpx.QueryParams = request.url.params
                    subject: str | None = (
                        "user@example.invalid" if corpus.startswith("user:") else None
                    )
                    queries.append(
                        json_object(
                            (
                                ("kind", json_str(kind.value)),
                                ("selector", _selector_json(selector)),
                                ("corpus_key", json_str(corpus)),
                                ("cursor", _optional(cursor)),
                                (
                                    "expected",
                                    json_object(
                                        (
                                            ("q", json_str(params.get("q", ""))),
                                            ("corpora", json_str(params.get("corpora", ""))),
                                            ("drive_id", _optional(params.get("driveId"))),
                                            ("page_token", _optional(params.get("pageToken"))),
                                            (
                                                "page_size",
                                                json_int(int(params.get("pageSize", "0"))),
                                            ),
                                            ("subject", _optional(subject)),
                                        )
                                    ),
                                ),
                            )
                        )
                    )
        selector = selectors[0]
        page: PartitionPage = await GoogleSweep(CollectorKind.GDRIVE, factory, None, 10)._root_page(
            selector
        )
        return json_object(
            (
                ("refs", json_array(refs)),
                ("shards", json_array(shards)),
                ("queries", json_array(queries)),
                ("root", json_array([json_str(x.corpus_key) for x in page.sub_corpora])),
            )
        )
    finally:
        await bundle.close()


async def sheet_segment_cases() -> JsonValue:
    """Capture Sheets ranges and segments through an offline collector."""
    titles: tuple[str, ...] = ("O'Brien", "Bang! tab", "Space tab")
    value_json: str = (
        '[{"range":"O\u0027Brien!A1","values":'
        '[[1,1.0,1e16,1e-5,0.0001,123456789.125,true,""],["x"],[]]}]'
    )

    def respond(request: httpx.Request) -> httpx.Response:
        path: str = request.url.path
        if path.endswith("/permissions"):
            return httpx.Response(200, json={"permissions": []})
        if path.endswith("/values:batchGet"):
            return httpx.Response(200, content='{"valueRanges":' + value_json + "}")
        if path.startswith("/v4/spreadsheets/"):
            return httpx.Response(
                200, json={"sheets": [{"properties": {"title": t}} for t in titles]}
            )
        return httpx.Response(
            200, json={"id": "fixture", "mimeType": "application/vnd.google-apps.spreadsheet"}
        )

    bundle: GoogleTestBundle = make_bundle(respond)
    try:
        adapter: GoogleAdapter = adapters_for(bundle)[2]
        content: ResourceContent = await adapter._fetch_content(
            drive_config(CollectorKind.GSHEETS), adapter._ref("fixture"), None
        )
        request: httpx.Request = bundle.api_requests[-1]
        segments: list[JsonValue] = [
            json_object(
                (("locator", json_str(segment.locator)), ("text_b64", json_str(_b64(segment.text))))
            )
            for segment in content.segments
        ]
        count: int = (
            content.items.count
            if content.items is not None and content.items.count is not None
            else 0
        )
        return json_array(
            [
                json_object(
                    (
                        ("titles", json_array([json_str(t) for t in titles])),
                        (
                            "ranges",
                            json_array(
                                [json_str(r) for r in request.url.params.get_list("ranges")]
                            ),
                        ),
                        ("value_ranges", parse_json(value_json)),
                        ("segments", json_array(segments)),
                        ("item_count", json_int(count)),
                    )
                )
            ]
        )
    finally:
        await bundle.close()


def _finding_json(finding: RawFinding) -> JsonValue:
    return json_object(
        (
            ("rule_id", json_str(finding.rule_id)),
            ("rule_class", json_str(finding.rule_class.value)),
            ("start", json_int(finding.span.start)),
            ("end", json_int(finding.span.end)),
            ("confidence", json_float(finding.confidence)),
        )
    )


def detector_cases() -> JsonValue:
    """Render detector outputs from the Python reference."""
    detector: RuleDetector = RuleDetector()
    picked: tuple[str, ...] = tuple(sample.value for sample in all_samples()) + (
        "aws_secret " + all_samples()[1].value,
        "+1-202-555-0123",
        "123 Main Street",
        "ＰＡＳＳＷＯＲＤ=correct-horse-battery",
        "İpassword=abcdefgh",
        "ıpassword=abcdefgh",
        "Kpassword=abcdefgh",
        "東京\n東京都千代田区千代田1-1",
        "placeholder password=changeme",
        "550e8400-e29b-41d4-a716-446655440000",
        "é" + all_samples()[0].value + "١",
        "aws_secret " + "x" * 79 + all_samples()[1].value,
        "aws_secret " + "x" * 81 + all_samples()[1].value,
        "eyJ" + "bad=.payload.signature",
        "eyJ" + "TmFO.payload.signature",
        all_samples()[10].value + " " + all_samples()[8].value,
        "+09012345678",
        "4111111111111111",
        "123 Maın Street",
        "123 Maİn Street",
        "123 Kelvin Street",
        "aws_secret" + " " * 70 + all_samples()[1].value,
        "aws_secret" + " " * 71 + all_samples()[1].value,
        "aws_secret " + "abcdefghijklmnop" * 2 + "abcdefgh",
        "aws_secret " + "abcdefghijklmnopq" * 2 + "abcdef",
        "eyJ" + "TmFOfQ.payload.signature",
    )
    cases: list[JsonValue] = []
    for text in picked + generated_detector_cases():
        cases.append(
            json_object(
                (
                    ("text_b64", json_str(_b64(text))),
                    ("findings", json_array([_finding_json(f) for f in detector.scan_text(text)])),
                )
            )
        )
    return json_array(cases)


def derived_fingerprint(
    rule_id: str, rule_class: RuleClass, raw: str, key: bytes
) -> tuple[str, int]:
    """Derive the first fingerprint accepted by the reference redactor."""
    normalized: str = Fingerprinter.normalize_for_fingerprint(raw, rule_class)
    message: str = f"v1\0{rule_id}\0{normalized}"
    redactor: Redactor = Redactor.default()
    for n in range(17):
        candidate: str = external_fingerprint(message if n == 0 else f"{message}\0{n}", key)
        if not redactor.has_hits(candidate):
            return candidate, n
    raise ValueError("no clean fixture fingerprint")


def fingerprint_cases(key: bytes) -> JsonValue:
    """Render SDK fingerprints and redactor-safe finding fingerprints."""
    values: tuple[str, ...] = ("", "fixture", "Ｆｉｘｔｕｒｅ", "line\nitem")
    external: list[JsonValue] = [
        json_object(
            (
                ("value_b64", json_str(_b64(value))),
                ("fp", json_str(external_fingerprint(value, key))),
            )
        )
        for value in values
    ]
    items: list[JsonValue] = [
        json_object(
            (
                ("item_key_b64", json_str(_b64(value))),
                ("fp", json_str(item_fingerprint(value, key))),
            )
        )
        for value in values
    ]
    inputs: list[tuple[str, RuleClass, str]] = [
        (sample.name, sample.rule_class, sample.value) for sample in all_samples()
    ]
    inputs.extend(
        (
            ("email", RuleClass.EMAIL, "UPPER" + "@example.invalid"),
            ("phone", RuleClass.PHONE, "０９０-１２３４-５６７８"),
            ("email", RuleClass.EMAIL, "e\u0301" + "@example.invalid"),
        )
    )
    found: int = 0
    n: int
    for i in range(10000):
        raw: str = f"case{i}" + "@example.invalid"
        _, n = derived_fingerprint("email", RuleClass.EMAIL, raw, key)
        if n:
            inputs.append(("email", RuleClass.EMAIL, raw))
            found += 1
            if found == 2:
                break
    if found < 2:
        raise ValueError("insufficient derived fingerprint fixtures")
    findings: list[JsonValue] = []
    for rule_id, rule_class, raw in inputs:
        fp: str
        n: int
        fp, n = derived_fingerprint(rule_id, rule_class, raw, key)
        normalized: str = Fingerprinter.normalize_for_fingerprint(raw, rule_class)
        findings.append(
            json_object(
                (
                    ("rule_id", json_str(rule_id)),
                    ("rule_class", json_str(rule_class.value)),
                    ("raw_b64", json_str(_b64(raw))),
                    ("normalized_b64", json_str(_b64(normalized))),
                    ("fp", json_str(fp)),
                    ("derived_n", json_int(n)),
                )
            )
        )
    return json_object(
        (
            ("key_b64", json_str(_b64(key.decode("utf-8")))),
            ("external", json_array(external)),
            ("item", json_array(items)),
            ("finding", json_array(findings)),
        )
    )


def redactor_cases() -> JsonValue:
    """Render redaction results for synthetic input and identifiers."""
    redactor: Redactor = Redactor.default()
    inputs: tuple[str, ...] = tuple(s.value for s in all_samples()) + (
        "abcdef0123456789" * 4,
        "user:" + "1" * 21,
        "drive-file-id_ABC123",
        "[REDACTED]",
        "****",
        "plain text",
    )
    return json_array(
        [
            json_object(
                (
                    ("text_b64", json_str(_b64(text))),
                    ("has_hits", json_bool(redactor.has_hits(text))),
                    ("redacted_b64", json_str(_b64(redactor.redact(text).text))),
                )
            )
            for text in inputs
        ]
    )


def sealing_cases(key: bytes) -> JsonValue:
    """Render masked findings and separately derived external fingerprints."""
    detector: RuleDetector = RuleDetector()
    masker: Masker = Masker(Fingerprinter(key, "fixture"), Redactor.default())
    texts: tuple[str, ...] = tuple(s.value for s in all_samples()) + (
        "x" * 220 + " " + all_samples()[8].value + " " + "y" * 220,
        all_samples()[8].value + " " + all_samples()[10].value,
        "password=" + all_samples()[0].value,
    )
    cases: list[JsonValue] = []
    for text in texts:
        locator: str = "fixture:line:1"
        raw: tuple[RawFinding, ...] = tuple(detector.scan_text(text))
        sealed: list[MaskedFinding] = masker.seal(
            ScanItem("item", "gdrive", locator, text), DetectionResult("item", raw)
        )
        findings: list[JsonValue] = []
        for finding, masked in zip(raw, sealed, strict=True):
            fp: str
            n: int
            fp, n = derived_fingerprint(
                finding.rule_id,
                finding.rule_class,
                text[finding.span.start : finding.span.end],
                key,
            )
            findings.append(
                json_object(
                    (
                        ("rule_id", json_str(finding.rule_id)),
                        ("rule_class", json_str(finding.rule_class.value)),
                        ("masked_snippet_b64", json_str(_b64(masked.masked_snippet))),
                        ("location", json_str(masked.location)),
                        ("confidence", json_float(masked.confidence)),
                        ("external_fingerprint", json_str(fp)),
                    )
                )
            )
        cases.append(
            json_object(
                (
                    ("text_b64", json_str(_b64(text))),
                    ("locator", json_str(locator)),
                    ("findings", json_array(findings)),
                )
            )
        )
    return json_array(cases)
