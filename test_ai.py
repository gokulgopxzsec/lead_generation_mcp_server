import requests

def test_ollama():
    url = "http://localhost:11434/api/generate"
    data = {
        "model": "qwen3:8b",
        "prompt": "Say 'Connection Successful'",
        "stream": False
    }
    try:
        print("📡 Attempting to reach Ollama...")
        response = requests.post(url, json=data)
        
        # Fixed the typo here!
        if response.status_code == 200: 
            print("✅ SUCCESS! Ollama replied:\n", response.json().get('response'))
        else:
            print(f"❌ FAILED: Server returned status {response.status_code}")
            print("Response:", response.text)
            
    except Exception as e:
        print(f"❌ CONNECTION ERROR: {e}")

if __name__ == "__main__":
    test_ollama()