"""Certificate discovery and verified TLS handshakes without network access."""

import io
import shutil
import ssl
import subprocess
from urllib.error import URLError

import certifi
import pytest

from pathnest import update


@pytest.fixture
def portable_trust(monkeypatch):
    """Simulate frozen OpenSSL with no usable build-machine CA paths."""
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    monkeypatch.delenv('SSL_CERT_DIR', raising=False)
    monkeypatch.setattr(update, 'SYSTEM_CA_FILES', ())
    original = ssl.create_default_context

    def create(**kwargs):
        if kwargs:
            return original(**kwargs)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(ssl, 'create_default_context', create)


@pytest.fixture
def local_ca(tmp_path):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('openssl is needed to generate a temporary test certificate')
    cert, key = tmp_path / 'ca.pem', tmp_path / 'key.pem'
    subprocess.run([
        openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=DNS:localhost',
        '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'keyUsage=critical,digitalSignature,keyCertSign,cRLSign',
    ], check=True, capture_output=True, timeout=15)
    return cert, key


def handshake(context, cert, key, hostname='localhost'):
    """Exercise OpenSSL verification over memory buffers instead of sockets."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = server_done = False
    for _ in range(20):
        try:
            client.do_handshake()
            client_done = True
        except ssl.SSLWantReadError:
            pass
        server_in.write(client_out.read())
        try:
            server.do_handshake()
            server_done = True
        except ssl.SSLWantReadError:
            pass
        client_in.write(server_out.read())
        if client_done and server_done:
            return
    pytest.fail('TLS handshake did not finish')


def test_system_ca_fixes_frozen_paths_and_keeps_hostname_verification(portable_trust, local_ca, monkeypatch):
    cert, key = local_ca
    monkeypatch.setattr(update, 'SYSTEM_CA_FILES', (str(cert),))
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('system trust must be preferred'))
    context = update._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    handshake(context, cert, key)
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(context, cert, key, hostname='wrong.example')


def test_bundled_roots_load_when_system_trust_is_missing(portable_trust, local_ca):
    context = update._tls_context()
    assert context.get_ca_certs()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(context, *local_ca)


def test_default_system_roots_are_kept(portable_trust, local_ca, monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(local_ca[0]))
    monkeypatch.setattr(ssl, 'create_default_context', lambda: context)
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('existing system trust must be kept'))
    assert update._tls_context() is context
    handshake(context, *local_ca)


def test_explicit_ca_bundle_is_honored(portable_trust, local_ca, monkeypatch):
    monkeypatch.setenv('SSL_CERT_FILE', str(local_ca[0]))
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('explicit trust must not use fallback'))
    context = update._tls_context()
    assert len(context.get_ca_certs()) == 1
    handshake(context, *local_ca)


def test_explicit_ca_directories_load_lazily(portable_trust, local_ca, tmp_path, monkeypatch):
    first, second = tmp_path / 'empty', tmp_path / 'hashed'
    first.mkdir()
    second.mkdir()
    digest = subprocess.check_output(['openssl', 'x509', '-hash', '-noout', '-in', str(local_ca[0])], text=True).strip()
    (second / (digest + '.0')).symlink_to(local_ca[0])
    monkeypatch.setenv('SSL_CERT_DIR', str(first) + ':' + str(second))
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('explicit trust must not use fallback'))
    context = update._tls_context()
    handshake(context, *local_ca)


@pytest.mark.parametrize('variable', ['SSL_CERT_FILE', 'SSL_CERT_DIR'])
def test_missing_explicit_trust_fails_without_fallback(portable_trust, tmp_path, monkeypatch, variable):
    monkeypatch.setenv(variable, str(tmp_path / 'missing'))
    monkeypatch.setattr(certifi, 'where', lambda: pytest.fail('bad explicit settings must not fall back'))
    with pytest.raises(update.UpdateError, match=variable):
        update._tls_context()


def test_invalid_system_bundle_is_actionable(portable_trust, tmp_path, monkeypatch):
    bundle = tmp_path / 'invalid.pem'
    bundle.write_text('not a certificate')
    monkeypatch.setattr(update, 'SYSTEM_CA_FILES', (str(bundle),))
    with pytest.raises(update.UpdateError, match='cannot load HTTPS certificates'):
        update._tls_context()


@pytest.mark.parametrize('wrapped', [False, True])
def test_certificate_errors_are_distinct_from_network_failure(portable_trust, tmp_path, monkeypatch, wrapped):
    class Opener:
        def open(self, *args, **kwargs):
            error = ssl.SSLCertVerificationError(1, 'certificate verify failed')
            raise URLError(error) if wrapped else error

    monkeypatch.setattr(update, 'build_opener', lambda *args: Opener())
    destination = tmp_path / 'response'
    with pytest.raises(update.UpdateError, match='certificate verification failed'):
        update._fetch(update.LATEST_URL, destination, 100)
    assert not destination.exists()


def test_fetch_uses_verified_context_for_api_and_assets(portable_trust, tmp_path, monkeypatch):
    seen = []

    def opener(*handlers):
        https = next(handler for handler in handlers if isinstance(handler, update.HTTPSHandler))
        assert https._context.check_hostname
        assert https._context.verify_mode == ssl.CERT_REQUIRED
        assert https._context.get_ca_certs()

        class Opener:
            def open(self, request, **kwargs):
                seen.append(request.full_url)
                return io.BytesIO(b'checked')

        return Opener()

    monkeypatch.setattr(update, 'build_opener', opener)
    urls = [update.LATEST_URL, update.RELEASES_URL + '/download/v1.3.0/' + update.ASSET]
    for url in urls:
        update._fetch(url, tmp_path / 'response', 100)
    assert seen == urls
