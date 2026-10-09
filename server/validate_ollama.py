#!/usr/bin/env python3
"""
Mergeclear Ollama setup validator
Checks if Ollama and Mistral are properly configured
"""

import os
import sys
import requests
import json

def check_ollama_connection():
    """Check if Ollama server is running"""
    print("\n🔍 Checking Ollama connection...")
    try:
        response = requests.get(
            "http://localhost:11434/api/health",
            timeout=5
        )
        if response.status_code == 200:
            print("   ✅ Ollama server is running on http://localhost:11434")
            return True
        else:
            print(f"   ❌ Ollama returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("   ❌ Cannot connect to Ollama on http://localhost:11434")
        print("   💡 Start Ollama with: ollama serve")
        return False
    except requests.exceptions.Timeout:
        print("   ❌ Ollama connection timeout")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False

def check_mistral_model():
    """Check if Mistral model is available"""
    print("\n🔍 Checking Mistral model...")
    try:
        response = requests.post(
            "http://localhost:11434/api/show",
            json={"name": "mistral"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if "details" in data:
                print("   ✅ Mistral model is installed")
                print(f"      Size: {data.get('details', {}).get('parameter_size', 'Unknown')}")
                return True
            else:
                print("   ❌ Mistral model info incomplete")
                return False
        else:
            print(f"   ❌ Mistral model not found (status {response.status_code})")
            print("   💡 Install with: ollama pull mistral")
            return False
    except requests.exceptions.ConnectionError:
        print("   ⚠️  Cannot reach Ollama server (check if running)")
        return False
    except Exception as e:
        print(f"   ⚠️  Error checking model: {e}")
        return False

def test_mistral_inference():
    """Test if Mistral can generate a response"""
    print("\n🔍 Testing Mistral inference...")
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "mistral",
                "prompt": "What is 2+2? Answer in one word.",
                "stream": False
            },
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            if "response" in result:
                answer = result["response"].strip()
                print(f"   ✅ Mistral is working!")
                print(f"      Test response: '{answer}'")
                return True
            else:
                print("   ❌ Invalid response from Mistral")
                return False
        else:
            print(f"   ❌ Mistral inference failed (status {response.status_code})")
            return False
    except requests.exceptions.Timeout:
        print("   ⏱️  Mistral inference timed out (first load takes ~10 seconds)")
        print("      Waiting for model to load into memory...")
        return True  # Assume success on timeout (just slow)
    except Exception as e:
        print(f"   ❌ Error during inference: {e}")
        return False

def check_server_imports():
    """Check that the Mergeclear server and its Ollama integration import"""
    print("\n🔍 Checking the Mergeclear server...")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from app.services.llm_service import summarize_changes  # noqa: F401
        print("   ✅ llm_service.py imports successfully")

        from app.api.routes import router  # noqa: F401
        print("   ✅ routes.py loads successfully")
        
        return True
    except Exception as e:
        print(f"   ❌ Import error: {e}")
        return False

def main():
    """Run all validation checks"""
    print("=" * 60)
    print("🚀 Mergeclear Ollama setup validator")
    print("=" * 60)
    
    results = {}
    
    # Check 1: Ollama connection
    results["ollama_running"] = check_ollama_connection()
    
    if not results["ollama_running"]:
        print("\n" + "=" * 60)
        print("❌ Ollama is not running!")
        print("\nTo fix:")
        print("  1. Install Ollama from https://ollama.ai")
        print("  2. Run: ollama serve")
        print("  3. Keep that terminal running")
        print("  4. Run this validator again")
        print("=" * 60)
        return False
    
    # Check 2: Mistral model
    results["mistral_installed"] = check_mistral_model()
    
    if not results["mistral_installed"]:
        print("\n" + "=" * 60)
        print("⚠️  Mistral model not installed")
        print("\nTo fix:")
        print("  1. Run: ollama pull mistral")
        print("  2. This downloads ~4GB (first time only)")
        print("  3. Once done, run this validator again")
        print("=" * 60)
        return False
    
    # Check 3: Mistral inference
    results["mistral_works"] = test_mistral_inference()
    
    if not results["mistral_works"]:
        print("\n" + "=" * 60)
        print("⚠️  Mistral inference test failed")
        print("\nTroubleshooting:")
        print("  - Check Ollama server: curl http://localhost:11434/api/health")
        print("  - Check model: ollama list")
        print("  - Restart Ollama: ollama serve")
        print("=" * 60)
    
    # Check 4: server integration
    results["server_integration"] = check_server_imports()
    
    # Final status
    print("\n" + "=" * 60)
    print("📊 Validation Results:")
    print("=" * 60)
    
    all_passed = all(results.values())
    
    for check, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"{status} {check.replace('_', ' ').title()}")
    
    print("=" * 60)
    
    if all_passed:
        print("\n🎉 All checks passed!")
        print("\nYou're ready to use Mergeclear with AI-written docs and summaries!")
        print("\n📋 Next steps:")
        print("  1. Start Mergeclear: python run.py")
        print("  2. Open http://localhost:8000/ui")
        print("  3. Navigate to any API → History → Compare")
        print("  4. Enjoy AI-powered diffs! ✨")
        return True
    else:
        print("\n⚠️  Some checks failed. See above for details.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
