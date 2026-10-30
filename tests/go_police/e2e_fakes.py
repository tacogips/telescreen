"""Loopback fake Google token, Drive, Sheets and Admin services for the Go police E2E.

One stdlib ``ThreadingHTTPServer`` on ``127.0.0.1`` port 0 serves every fake endpoint,
routed by path. The handler class is built by a factory closure over a frozen
``FakeEstate``. The RSA key is generated at runtime and the service-account JSON is
written to the test's ``tmp_path``; no key material is committed.
"""

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from socketserver import BaseServer
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from pydantic import TypeAdapter

from tests.support.samples import (
    aws_access_key_id,
    credit_card,
    email_address,
    password_assignment,
)

DRIVE_SCOPE: str = "https://www.googleapis.com/auth/drive.readonly"
SHEETS_SCOPE: str = "https://www.googleapis.com/auth/spreadsheets.readonly"
ADMIN_SCOPE: str = "https://www.googleapis.com/auth/admin.directory.user.readonly"
JWT_BEARER_GRANT: str = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_EMAIL: str = "police-e2e@service.example.invalid"
DOC_MIME: str = "application/vnd.google-apps.document"
SHEET_MIME: str = "application/vnd.google-apps.spreadsheet"

type CellValue = str | int | bool
type QueryPairs = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FakePermission:
    """One Drive permission entry served by ``permissions.list``."""

    kind: str
    role: str
    email: str | None = None
    domain: str | None = None
    discoverable: bool = False


@dataclass(frozen=True)
class FakeSheet:
    """One spreadsheet tab with formatted cell values."""

    title: str
    rows: tuple[tuple[CellValue, ...], ...]


@dataclass(frozen=True)
class FakeFile:
    """One Drive file of the fake estate.

    ``trashed`` describes ``files.get`` metadata only: listings always report the file as
    not trashed, which models a file trashed between enumeration and scan.
    """

    file_id: str
    name: str
    mime_type: str
    owner: str | None
    drive_id: str | None
    content: str = field(default="", repr=False)
    permissions: tuple[FakePermission, ...] = ()
    sheet: FakeSheet | None = None
    gone: bool = False
    trashed: bool = False

    @property
    def parents(self) -> tuple[str, ...]:
        """Return the parent folder (the shared drive root or the owner's root)."""
        return (self.drive_id,) if self.drive_id is not None else ("root",)


@dataclass(frozen=True)
class FakeUser:
    """One directory user."""

    user_id: str
    email: str


@dataclass(frozen=True)
class TokenGrant:
    """The verified subject and scope of one token request."""

    subject: str | None
    scope: str


@dataclass(frozen=True)
class IssuedToken:
    """An access token handed out by the fake token endpoint."""

    token: str = field(repr=False)
    grant: TokenGrant


@dataclass(frozen=True)
class RecordedCall:
    """One authenticated Google API request seen by the fake."""

    method: str
    path: str
    subject: str | None


@dataclass(frozen=True)
class JwtClaims:
    """Claims of a verified service-account JWT assertion."""

    iss: str
    scope: str
    aud: str
    iat: int
    exp: int
    sub: str | None = None


@dataclass(frozen=True)
class FakeResponse:
    """A response the handler writes back."""

    status: int
    body: bytes = field(repr=False)
    content_type: str = "application/json"


@dataclass(frozen=True)
class FakeEstate:
    """A delegated Workspace estate plus the requests recorded against it."""

    admin: str
    users: tuple[FakeUser, ...]
    drive_id: str
    files: tuple[FakeFile, ...]
    public_key: rsa.RSAPublicKey = field(repr=False)
    grants: list[TokenGrant] = field(default_factory=list)
    issued: list[IssuedToken] = field(default_factory=list, repr=False)
    calls: list[RecordedCall] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def issue(self, grant: TokenGrant) -> str:
        """Record a verified grant and return a fresh opaque access token."""
        token: str = "fake-access-" + secrets.token_hex(16)
        with self.lock:
            self.grants.append(grant)
            self.issued.append(IssuedToken(token, grant))
        return token

    def grant_for(self, authorization: str | None) -> TokenGrant | None:
        """Resolve a bearer header to the grant it was issued for."""
        if authorization is None or not authorization.startswith("Bearer "):
            return None
        presented: str = authorization.removeprefix("Bearer ")
        with self.lock:
            return next((item.grant for item in self.issued if item.token == presented), None)

    def note(self, problem: str) -> None:
        """Record a request the estate does not expect (no content, only shape)."""
        with self.lock:
            self.unexpected.append(problem)

    def record(self, call: RecordedCall) -> None:
        """Record one authenticated API call."""
        with self.lock:
            self.calls.append(call)

    def file(self, file_id: str) -> FakeFile | None:
        """Return the file with this id, if it exists in the estate."""
        return next((item for item in self.files if item.file_id == file_id), None)

    def user_email(self, user_id: str) -> str | None:
        """Return a directory user's primary e-mail."""
        return next((user.email for user in self.users if user.user_id == user_id), None)


@dataclass(frozen=True)
class FakeGoogle:
    """A running loopback fake Google server."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    base_url: str

    @property
    def token_uri(self) -> str:
        """Return the token endpoint URL used as the JWT audience."""
        return self.base_url + "/token"

    def close(self) -> None:
        """Stop serving, close the socket and join the serving thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


def generate_private_key() -> rsa.RSAPrivateKey:
    """Generate a throwaway RSA key for this test run."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_service_account(directory: Path, private_key: rsa.RSAPrivateKey, token_uri: str) -> Path:
    """Write a service-account key file that points at the fake token endpoint."""
    pem: str = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    path: Path = directory / "service-account.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": CLIENT_EMAIL,
                "private_key": pem,
                "token_uri": token_uri,
            }
        )
    )
    path.chmod(0o600)
    return path


def default_estate(public_key: rsa.RSAPublicKey) -> FakeEstate:
    """Build the delegated estate: admin, users u1 and u2, one shared drive, six files."""
    domain: str = "example.com"
    drive_id: str = "0AExampleDrive"
    u1: FakeUser = FakeUser("u1", "u1@" + domain)
    u2: FakeUser = FakeUser("u2", "u2@" + domain)
    owner_u1: FakePermission = FakePermission("user", "owner", email=u1.email)
    owner_u2: FakePermission = FakePermission("user", "owner", email=u2.email)
    text: str = (
        "Quarterly export\n"
        f"aws_access_key_id = {aws_access_key_id().value}\n"
        f"contact: {email_address().value}\n"
    )
    files: tuple[FakeFile, ...] = (
        FakeFile(
            "f-text",
            "q3-payroll-export.txt",
            "text/plain",
            u1.email,
            None,
            text,
            (owner_u1, FakePermission("anyone", "reader")),
        ),
        FakeFile(
            "f-doc",
            "Onboarding Runbook Draft",
            DOC_MIME,
            u2.email,
            None,
            f"Onboarding\n{password_assignment().value}\n",
            (owner_u2, FakePermission("user", "reader", email="x@partner.example.org")),
        ),
        FakeFile(
            "f-sheet",
            "Card Ledger Archive",
            SHEET_MIME,
            None,
            drive_id,
            permissions=(
                FakePermission("domain", "reader", domain=domain),
                FakePermission("user", "organizer", email=u1.email),
            ),
            sheet=FakeSheet(
                "Cards",
                (
                    ("holder", "card", "active"),
                    (),
                    ("primary", credit_card().value, True),
                    ("backup", 42, False),
                ),
            ),
        ),
        FakeFile("f-image", "team-offsite-photo.png", "image/png", u1.email, None, "", (owner_u1,)),
        FakeFile(
            "f-gone",
            "removed-meeting-notes.txt",
            "text/plain",
            u1.email,
            None,
            "",
            (owner_u1,),
            gone=True,
        ),
        FakeFile(
            "f-trashed",
            "trashed-draft-notes.txt",
            "text/plain",
            u1.email,
            None,
            "",
            (owner_u1,),
            trashed=True,
        ),
    )
    return FakeEstate("admin@" + domain, (u1, u2), drive_id, files, public_key)


def start_fake_google(estate: FakeEstate) -> FakeGoogle:
    """Serve the estate on a loopback port in a daemon thread."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(estate))
    server.daemon_threads = True
    thread: threading.Thread = threading.Thread(
        target=server.serve_forever, name="fake-google", daemon=True
    )
    thread.start()
    return FakeGoogle(server, thread, f"http://127.0.0.1:{server.server_port}")


def _handler_class(estate: FakeEstate) -> type[BaseHTTPRequestHandler]:
    """Build a request handler class bound to one estate."""

    class FakeGoogleHandler(BaseHTTPRequestHandler):
        """Route one loopback request to the fake estate."""

        def do_GET(self) -> None:
            """Serve a Drive, Sheets or Admin read."""
            try:
                response: FakeResponse = _route_get(
                    estate, self.path, self.headers.get("Authorization")
                )
            except Exception as error:
                estate.note(f"GET handler error {type(error).__name__}")
                response = _error(500, "backendError")
            self._send(response)

        def do_POST(self) -> None:
            """Serve the OAuth token endpoint."""
            length: int = int(self.headers.get("Content-Length") or "0")
            body: bytes = self.rfile.read(length)
            try:
                response: FakeResponse = (
                    _token(estate, body, _audience(self.server))
                    if urlsplit(self.path).path == "/token"
                    else _unexpected(estate, "POST " + urlsplit(self.path).path)
                )
            except Exception as error:
                estate.note(f"POST handler error {type(error).__name__}")
                response = _error(500, "backendError")
            self._send(response)

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            """Keep the test output free of access logs."""
            del code, size

        def _send(self, response: FakeResponse) -> None:
            """Write a complete response with an explicit length."""
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

    return FakeGoogleHandler


def _audience(server: BaseServer) -> str:
    """Return the token URI this server answers for."""
    if not isinstance(server, HTTPServer):
        raise TypeError("fake Google requires an HTTP server")
    return f"http://127.0.0.1:{server.server_port}/token"


def _json(status: int, text: str) -> FakeResponse:
    """Return a JSON response."""
    return FakeResponse(status, text.encode())


def _error(status: int, reason: str) -> FakeResponse:
    """Return a Google-style error envelope."""
    return _json(status, json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}}))


def _unexpected(estate: FakeEstate, problem: str) -> FakeResponse:
    """Record an unexpected request and fail it without a gone status."""
    estate.note(problem)
    return _error(400, "badRequest")


def _param(query: QueryPairs, name: str) -> str | None:
    """Return the first value of a query parameter."""
    return next((value for key, value in query if key == name), None)


def _token(estate: FakeEstate, body: bytes, audience: str) -> FakeResponse:
    """Verify an RS256 JWT-bearer assertion and issue an access token."""
    form: QueryPairs = tuple(parse_qsl(body.decode(), keep_blank_values=True))
    assertion: str | None = _param(form, "assertion")
    if _param(form, "grant_type") != JWT_BEARER_GRANT or assertion is None:
        return _unexpected(estate, "token request without a JWT-bearer assertion")
    try:
        claims: JwtClaims = TypeAdapter(JwtClaims).validate_python(
            jwt.decode(
                assertion,
                key=estate.public_key,
                algorithms=["RS256"],
                audience=audience,
                leeway=30,
            )
        )
    except jwt.PyJWTError as error:
        estate.note(f"token assertion rejected {type(error).__name__}")
        return _json(400, json.dumps({"error": "invalid_grant"}))
    if claims.iss != CLIENT_EMAIL or claims.scope not in (DRIVE_SCOPE, SHEETS_SCOPE, ADMIN_SCOPE):
        return _unexpected(estate, "token request with an unexpected issuer or scope")
    known: tuple[str, ...] = (estate.admin, *(user.email for user in estate.users))
    if claims.sub is not None and claims.sub not in known:
        return _unexpected(estate, "token request for an unknown subject")
    token: str = estate.issue(TokenGrant(claims.sub, claims.scope))
    return _json(
        200, json.dumps({"access_token": token, "expires_in": 3600, "token_type": "Bearer"})
    )


def _route_get(estate: FakeEstate, raw_path: str, authorization: str | None) -> FakeResponse:
    """Dispatch one authenticated GET by path."""
    parts: SplitResult = urlsplit(raw_path)
    path: str = unquote(parts.path)
    query: QueryPairs = tuple(parse_qsl(parts.query, keep_blank_values=True))
    grant: TokenGrant | None = estate.grant_for(authorization)
    if grant is None:
        estate.note("GET without a valid access token " + path)
        return _error(401, "authError")
    estate.record(RecordedCall("GET", path, grant.subject))
    if path.startswith("/admin/directory/v1/users"):
        return _admin(estate, grant, path, query)
    if path == "/drive/v3/drives":
        return _drives(estate, grant)
    if path == "/drive/v3/files":
        return _files_list(estate, grant, query)
    if path.startswith("/drive/v3/files/"):
        return _file_request(estate, grant, path.removeprefix("/drive/v3/files/"), query)
    if path.startswith("/v4/spreadsheets/"):
        return _sheets(estate, grant, path.removeprefix("/v4/spreadsheets/"), query)
    return _unexpected(estate, "GET " + path)


def _denied(estate: FakeEstate, problem: str) -> FakeResponse:
    """Record and refuse a request whose scope or subject does not fit."""
    estate.note(problem)
    return _error(403, "insufficientPermissions")


def _admin(estate: FakeEstate, grant: TokenGrant, path: str, query: QueryPairs) -> FakeResponse:
    """Serve ``users.list`` and ``users.get`` for the delegated admin only."""
    if grant.scope != ADMIN_SCOPE or grant.subject != estate.admin:
        return _denied(estate, "admin request without the delegated admin grant")
    if path == "/admin/directory/v1/users":
        if _param(query, "customer") != "my_customer":
            return _unexpected(estate, "users.list without customer=my_customer")
        return _json(
            200,
            json.dumps(
                {"users": [{"id": u.user_id, "primaryEmail": u.email} for u in estate.users]}
            ),
        )
    user_id: str = path.removeprefix("/admin/directory/v1/users/")
    email: str | None = estate.user_email(user_id)
    if email is None:
        return _unexpected(estate, "users.get for an unknown user")
    return _json(200, json.dumps({"id": user_id, "primaryEmail": email}))


def _drives(estate: FakeEstate, grant: TokenGrant) -> FakeResponse:
    """Serve ``drives.list`` as the delegated admin."""
    if grant.scope != DRIVE_SCOPE or grant.subject != estate.admin:
        return _denied(estate, "drives.list without the admin drive grant")
    return _json(200, json.dumps({"drives": [{"id": estate.drive_id, "name": "Finance"}]}))


def _file_meta_json(file: FakeFile, trashed: bool) -> str:
    """Serialize file metadata with the members the worker requests."""
    return json.dumps(
        {
            "id": file.file_id,
            "name": file.name,
            "mimeType": file.mime_type,
            "trashed": trashed,
            "driveId": file.drive_id,
            "parents": list(file.parents),
            "owners": [{"emailAddress": file.owner}] if file.owner is not None else [],
            "modifiedTime": "2026-09-30T12:00:00.000Z",
            "size": str(len(file.content.encode())),
        }
    )


def _files_list(estate: FakeEstate, grant: TokenGrant, query: QueryPairs) -> FakeResponse:
    """Serve one ``files.list`` page for a user or shared-drive corpus."""
    if grant.scope != DRIVE_SCOPE:
        return _denied(estate, "files.list without the drive scope")
    corpora: str | None = _param(query, "corpora")
    q: str = _param(query, "q") or ""
    candidates: tuple[FakeFile, ...]
    if corpora == "user":
        if grant.subject is None or not q.startswith("'me' in owners and trashed = false"):
            return _unexpected(estate, "user files.list without subject or owner query")
        candidates = tuple(
            item for item in estate.files if item.owner == grant.subject and item.drive_id is None
        )
    elif corpora == "drive":
        if _param(query, "driveId") != estate.drive_id or not q.startswith("trashed = false"):
            return _unexpected(estate, "drive files.list for an unknown drive or query")
        candidates = tuple(item for item in estate.files if item.drive_id == estate.drive_id)
    else:
        return _unexpected(estate, "files.list with unsupported corpora")
    mime: re.Match[str] | None = re.search(r"mimeType = '([^']+)'", q)
    if mime is not None:
        candidates = tuple(item for item in candidates if item.mime_type == mime.group(1))
    listed: str = ",".join(_file_meta_json(item, False) for item in candidates)
    return _json(200, '{"files":[' + listed + "]}")


def _visible(estate: FakeEstate, grant: TokenGrant, file_id: str) -> FakeFile | None:
    """Return the file when the grant's identity can see it."""
    file: FakeFile | None = estate.file(file_id)
    if file is None:
        estate.note("request for a file outside the estate")
        return None
    if file.drive_id is None and file.owner != grant.subject:
        estate.note("file request with a subject that does not own the file")
        return None
    return file


def _file_request(
    estate: FakeEstate, grant: TokenGrant, rest: str, query: QueryPairs
) -> FakeResponse:
    """Serve ``files.get`` (metadata or media), ``files.export`` and ``permissions.list``."""
    if grant.scope != DRIVE_SCOPE:
        return _denied(estate, "file request without the drive scope")
    file_id: str
    action: str
    file_id, _, action = rest.partition("/")
    file: FakeFile | None = _visible(estate, grant, file_id)
    if file is None or file.gone:
        return _error(404, "notFound")
    if action == "permissions":
        return _json(
            200,
            json.dumps(
                {
                    "permissions": [
                        {
                            "type": item.kind,
                            "role": item.role,
                            "emailAddress": item.email,
                            "domain": item.domain,
                            "allowFileDiscovery": item.discoverable,
                        }
                        for item in file.permissions
                    ]
                }
            ),
        )
    if action == "export":
        if file.mime_type != DOC_MIME or _param(query, "mimeType") != "text/plain":
            return _unexpected(estate, "export of a non-Doc file or to a non-text type")
        return FakeResponse(200, file.content.encode(), "text/plain; charset=utf-8")
    if action != "":
        return _unexpected(estate, "unsupported file action")
    if _param(query, "alt") == "media":
        if file.mime_type != "text/plain" or file.trashed:
            return _unexpected(estate, "download of a non-text or trashed file")
        return FakeResponse(200, file.content.encode(), "text/plain")
    return _json(200, _file_meta_json(file, file.trashed))


def _sheets(estate: FakeEstate, grant: TokenGrant, rest: str, query: QueryPairs) -> FakeResponse:
    """Serve ``spreadsheets.get`` titles and ``values:batchGet``."""
    if grant.scope != SHEETS_SCOPE:
        return _denied(estate, "sheets request without the sheets scope")
    spreadsheet_id: str
    action: str
    spreadsheet_id, _, action = rest.partition("/")
    file: FakeFile | None = _visible(estate, grant, spreadsheet_id)
    if file is None or file.sheet is None:
        return _error(404, "notFound")
    sheet: FakeSheet = file.sheet
    if action == "":
        return _json(200, json.dumps({"sheets": [{"properties": {"title": sheet.title}}]}))
    expected: str = "'" + sheet.title.replace("'", "''") + "'!A:ZZZ"
    if action != "values:batchGet" or _param(query, "ranges") != expected:
        return _unexpected(estate, "values request with an unexpected action or range")
    if _param(query, "valueRenderOption") != "FORMATTED_VALUE":
        return _unexpected(estate, "values request without FORMATTED_VALUE")
    return _json(
        200,
        json.dumps(
            {
                "spreadsheetId": spreadsheet_id,
                "valueRanges": [
                    {
                        "range": f"'{sheet.title}'!A1:C{len(sheet.rows)}",
                        "majorDimension": "ROWS",
                        "values": [list(row) for row in sheet.rows],
                    }
                ],
            }
        ),
    )
