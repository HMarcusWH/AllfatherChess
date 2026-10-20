"""RELEASE-2B-2 signed index provenance destructive parsing tests."""
from __future__ import annotations
import importlib.util,lzma,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("native_signed_index",
                                           ROOT/"scripts/verify-release-native-index-chain.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def sample_release():
    hashes="".join(" "+"a"*64+f" {100+i} main/binary-amd64/index-{i}.xz\n"
                   for i in range(11))
    return ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
            "Origin: Ubuntu\nSuite: noble\nSHA256:\n"+hashes+
            "-----BEGIN PGP SIGNATURE-----\nFAKE\n-----END PGP SIGNATURE-----\n").encode()
def stanza(name="openssl",sha=None,extra=""):
    sha=sha or "a"*64
    return (f"Package: {name}\nVersion: 3.0.13-0ubuntu3.16\n"
            "Architecture: amd64\nFilename: pool/main/o/openssl/openssl_3.0.13-0ubuntu3.16_amd64.deb\n"
            f"Size: 111\nSHA256: {sha}\n"+extra+"\n").encode()
class IndexChainNegatives(unittest.TestCase):
    def test_valid_signed_release_index_catalogue(self):
        d=m.parse_sha256_inrelease(sample_release())
        self.assertEqual(len(d),11)
        self.assertEqual(d["main/binary-amd64/index-1.xz"],("a"*64,101))
    def test_bad_or_duplicate_signed_release_paths_refused(self):
        orig=sample_release()
        changes=[
            orig.replace(b"SHA256:",b"SHA1:"),
            orig.replace(b"main/binary-amd64/index-1.xz",b"../outside/index.xz"),
            orig.replace(b"main/binary-amd64/index-2.xz",b"main/binary-amd64/index-1.xz"),
            orig.replace(b" "+b"a"*64,b" "+b"c"*62+b"xx",1),
        ]
        for x in changes:
            with self.subTest(mutation=x[:40]),self.assertRaises(ValueError):
                m.parse_sha256_inrelease(x)
    def test_signed_index_single_control_stanza_identity(self):
        record=list(m.parse_debian_index(stanza()))
        self.assertEqual(len(record),1)
        self.assertEqual(record[0]["SHA256"],"a"*64)
        self.assertEqual(record[0]["Package"],"openssl")
        self.assertEqual(record[0]["Size"],"111")
    def test_duplicate_control_field_refused_even_when_other_record_is_valid(self):
        for evil in ("SHA256: "+"b"*64+"\n", "Version: 4\n",
                     " Package sha multline continuation\n"):
            with self.subTest(evil=evil),self.assertRaises(ValueError):
                list(m.parse_debian_index(stanza(extra=evil)))
    def test_compressed_package_index_bounded_and_rejects_trailing(self):
        plain=stanza()
        blob=lzma.compress(plain,format=lzma.FORMAT_XZ)
        self.assertEqual(m.decompress_index(blob),plain)
        self.assertEqual(m.decompress_index(blob+b"\x00"*4),plain)
        self.assertEqual(m.decompress_index(blob+blob),plain+plain)
        self.assertEqual(m.decompress_index(blob+b"\x00"*4+blob),plain+plain)
        with self.assertRaises(ValueError):
            m.decompress_index(blob+b"\x00"*3)
        for bad in (blob+b"tail",blob[:20],b"not-an-xz"):
            with self.subTest(bad=bad[:10]),self.assertRaises(ValueError):
                m.decompress_index(bad)
    def test_index_raw_bytes_must_match_signed_sha_size(self):
        compressed=lzma.compress(stanza())
        target=(m.digest(compressed),len(compressed))
        self.assertEqual(target,(m.digest(compressed),len(compressed)))
        self.assertNotEqual(target,(m.digest(compressed+b"x"),len(compressed)+1))
    def test_bounded_transport_read_timeout_does_not_admit_source(self):
        from unittest.mock import patch
        url=m.BASE+"/dists/noble/InRelease"
        with patch.object(m.urllib.request,"urlopen",
                          side_effect=TimeoutError("read timed out")) as opener, \
             patch.object(m.time,"sleep") as sleeper:
            with self.assertRaisesRegex(ValueError,
                                        "transport timeout refused after 5"):
                m.get(url,m.MAX_RELEASE)
        self.assertEqual(opener.call_count,5)
        self.assertEqual(sleeper.call_count,4)

    def test_unclassified_server_http_500_is_immediate_failure(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        url=m.BASE+"/dists/noble/InRelease"
        err=HTTPError(url,500,"Server Error",{},None)
        with patch.object(m.urllib.request,"urlopen",side_effect=err) as opener:
            with self.assertRaisesRegex(ValueError,"HTTP 500 refused after 1"):
                m.get(url,m.MAX_RELEASE)
        self.assertEqual(opener.call_count,1)

    def test_expected_suite_and_snapshot_are_constant(self):
        self.assertEqual(m.SNAP,"20261001T000000Z")
        self.assertEqual(len(m.SUITES),4)
        self.assertEqual(m.BASE,"https://snapshot.ubuntu.com/ubuntu/20261001T000000Z")
if __name__=="__main__":unittest.main()
