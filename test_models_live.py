import urllib.request
import json
import sys

key = "xpl_1e46b04dfeca9bcf4e28e55647e12fbb7e1e2a14"
headers = {
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
}

def test_model(m):
    url = "https://api.experientiallabs.ai/v1/chat/completions"
    body = json.dumps({
        "model": m,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 10
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            print(f"[SUCCESS 200] Model '{m}' HOẠT ĐỘNG! Reply: {data['choices'][0]['message']['content'][:30]}", flush=True)
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[{e.code}] {m}: {err_body[:120]}", flush=True)
        return False
    except Exception as ex:
        print(f"[ERR] {m}: {ex}", flush=True)
        return False

print("=== BẮT ĐẦU TEST CÁC MODEL ===", flush=True)
test_model("claude-fable-5.1")
test_model("claude-fable-latest")
test_model("claude-fable-5")
test_model("gpt-6-astra")
test_model("qwen3.8-27b")
test_model("openrouter-free")
test_model("nemotron-3-ultra-550b-a55b-free")
test_model("minimax-m3-free")
test_model("gemma-4-26b-a4b-it-free")
