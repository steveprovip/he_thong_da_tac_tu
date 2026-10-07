"""
Script kiểm tra kết nối API ExperientialLabs và liệt kê model khả dụng (bao gồm Claude Fable 5.1).
Sử dụng:
    python test_experientiallabs.py xpl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
hoặc:
    set EXPLABS_API_KEY=xpl_...
    python test_experientiallabs.py
"""
import sys
import os
import json
import urllib.request
import urllib.error

def main():
    api_key = None
    if len(sys.argv) > 1:
        api_key = sys.argv[1].strip()
    else:
        api_key = os.environ.get("EXPLABS_API_KEY", "").strip()

    if not api_key:
        print("[-] CHƯA CÓ API KEY!")
        print("    Vui lòng truyền key vào lệnh: python test_experientiallabs.py xpl_xxxx...")
        print("    Hoặc đặt biến môi trường: set EXPLABS_API_KEY=xpl_xxxx...")
        print("    Lấy key miễn phí tại: https://platform.experientiallabs.ai/api-keys")
        return

    print(f"[*] Đang kiểm tra API Key: {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else ''}")
    
    # 1. Test GET /v1/models
    models_url = "https://api.experientiallabs.ai/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "ExperientialLabs-Tester/1.0"
    }

    try:
        req = urllib.request.Request(models_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("[+] KẾT NỐI THÀNH CÔNG ĐẾN GATEWAY EXPERIENTIALLABS!")
            
            # Print available models
            model_list = []
            if isinstance(data, dict) and "data" in data:
                model_list = [m.get("id", "") for m in data["data"]]
            elif isinstance(data, list):
                model_list = [m.get("id", "") if isinstance(m, dict) else str(m) for m in data]
            
            print(f"[+] Tìm thấy {len(model_list)} models:")
            found_fable = False
            for m in model_list:
                if "fable" in m.lower():
                    print(f"    ⭐ {m}  <-- MODEL BẠN CẦN!")
                    found_fable = True
                else:
                    print(f"    - {m}")
            
            target_model = "claude-fable-5-1"
            if not found_fable and model_list:
                target_model = model_list[0]
                print(f"[!] Chú ý: Không thấy 'fable' trong tên, dùng thử model đầu tiên: {target_model}")
            
            # 2. Test chat completion
            print(f"\n[*] Đang gửi test completion với model '{target_model}'...")
            chat_url = "https://api.experientiallabs.ai/v1/chat/completions"
            body = json.dumps({
                "model": target_model,
                "messages": [{"role": "user", "content": "Reply with the single word: READY"}],
                "max_tokens": 16
            }).encode("utf-8")
            
            chat_headers = headers.copy()
            chat_headers["Content-Type"] = "application/json"
            
            req_chat = urllib.request.Request(chat_url, data=body, headers=chat_headers)
            with urllib.request.urlopen(req_chat, timeout=15) as chat_resp:
                chat_res = json.loads(chat_resp.read().decode("utf-8"))
                reply = chat_res.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(f"[+] PHẢN HỒI THÀNH CÔNG TỪ {target_model}: {reply.strip()}")
                print("\n[OK] API HOẠT ĐỘNG HOÀN HẢO! BẠN CÓ THỂ ĐIỀN THÔNG TIN NÀY VÀO CLINE NGAY!")
                
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[-] HTTP Error {e.code}: {e.reason}")
        print(f"    Chi tiết: {err_body}")
        if e.code == 401:
            print("    -> Key không hợp lệ hoặc đã hết hạn. Hãy kiểm tra lại tại https://platform.experientiallabs.ai/api-keys")
    except Exception as e:
        print(f"[-] Lỗi kết nối: {e}")

if __name__ == "__main__":
    main()
