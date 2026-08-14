from pipeline.tts.engines import vieneu, vieneu_frozen


def test_vieneu_removes_only_broken_ipv6_no_proxy_entries() -> None:
    expected = "127.0.0.1,localhost,example.com"
    for sanitize in (vieneu._sanitize_no_proxy, vieneu_frozen._sanitize_no_proxy):
        env = {
            "NO_PROXY": "127.0.0.1,localhost,::1,::1/128,example.com",
            "no_proxy": "127.0.0.1,localhost,[::1],[::1]/128,example.com",
        }
        sanitize(env)
        assert env == {"NO_PROXY": expected, "no_proxy": expected}
