"""
LLM Service for generating summaries of API documentation changes
and RAG-based search using local Ollama with Mistral model (no API keys required)
"""
import requests
import os
from typing import Optional, List, Dict
from app.config import DOCS_DIR, OLLAMA_URL, OLLAMA_MODEL


def summarize_changes(v1_content: str, v2_content: str, api_name: str) -> Optional[str]:
    """
    Generate an LLM-based summary of changes between two versions using local Ollama.
    
    Args:
        v1_content: Markdown content of version 1
        v2_content: Markdown content of version 2
        api_name: Name of the API being documented
        
    Returns:
        Summary string or None if Ollama is not running
        
    Requirements:
        - Ollama running locally on http://localhost:11434
        - Mistral model pulled: ollama pull mistral
    """
    
    # Trim content to reasonable size for prompt
    v1_preview = v1_content[:1500]
    v2_preview = v2_content[:1500]
    
    prompt = f"""You are an expert API documentation analyst. Compare these two API documentation versions and provide a concise summary of what changed.

VERSION 1:
{v1_preview}

VERSION 2:
{v2_preview}

Provide a brief professional summary highlighting:
1. New endpoints or parameters added
2. Removed or deprecated endpoints or parameters  
3. Changed request/response formats
4. Changes in behavior or requirements
5. Breaking changes or migration notes

Keep it concise - maximum 5 bullet points, under 150 words. Use professional language."""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )
        
        response.raise_for_status()
        result = response.json()
        summary = result.get("response", "").strip()
        
        if summary:
            return summary
        return None
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Ollama not running. Start with: ollama serve")
        return None
    except requests.exceptions.Timeout:
        print("❌ Ollama request timed out")
        return None
    except Exception as e:
        print(f"⚠️ Error generating summary: {e}")
        return None


def calculate_api_confidence(query: str, api: Dict) -> float:
    """
    Calculate confidence score for how well an API matches a query.
    Score ranges from 0.0 to 1.0
    
    Args:
        query: User's search query
        api: API dict with 'api' and 'repo' keys
        
    Returns:
        Confidence score (0.0 to 1.0)
    """
    query_lower = query.lower()
    api_name_lower = api.get("api", "").lower()
    repo_lower = api.get("repo", "").lower()
    
    # Split query into words for analysis
    query_words = query_lower.split()
    
    # Base score
    score = 0.0
    
    # Exact match = highest confidence
    if api_name_lower == query_lower:
        return 1.0
    
    # Full name substring match
    if api_name_lower in query_lower or query_lower in api_name_lower:
        score += 0.8
    
    # Word-level matching (each matching word increases score)
    matching_words = sum(1 for word in query_words if word in api_name_lower)
    if matching_words > 0:
        word_match_score = min(0.6, matching_words * 0.15)
        score = max(score, word_match_score)
    
    # Partial word matching (e.g., "branch" in "getBranchDetails")
    for word in query_words:
        # Remove common words
        if word in ["how", "to", "get", "all", "and", "or", "the", "a", "an"]:
            continue
        
        if len(word) >= 4 and word in api_name_lower:  # Minimum 4 chars for partial match
            score = max(score, 0.7)
            break
    
    # Check for related terms (synonyms)
    synonyms = {
        "get": ["fetch", "retrieve", "query", "list"],
        "create": ["add", "new", "insert", "post"],
        "update": ["modify", "edit", "put", "patch"],
        "delete": ["remove", "destroy", "drop"],
        "branches": ["branch", "branchdetails"],
        "users": ["user"],
        "api": ["endpoint", "service"]
    }
    
    for query_word in query_words:
        if query_word in synonyms:
            for synonym in synonyms[query_word]:
                if synonym in api_name_lower:
                    score = max(score, 0.65)
    
    # Penalize if repo is very different from query context
    if repo_lower and repo_lower != "":
        if repo_lower not in query_lower and query_lower not in repo_lower:
            score *= 0.9  # Slight penalty for different repo
    
    return min(1.0, score)


def search_apis_rag(query: str, base_path: str = DOCS_DIR) -> List[Dict]:
    """
    Search for APIs using RAG (Retrieval Augmented Generation) with local LLM.
    Finds relevant APIs based on semantic understanding of user query.
    
    Args:
        query: Natural language search query (e.g., "user management API")
        base_path: Base directory containing API documentation
        
    Returns:
        List of dictionaries with matched APIs: [{"repo": "...", "api": "...", "version": "..."}]
        
    Requirements:
        - Ollama running locally on http://localhost:11434
        - Mistral model pulled: ollama pull mistral
    """
    
    # Step 1: Collect all available APIs
    available_apis = []
    
    if os.path.exists(base_path):
        for repo in os.listdir(base_path):
            if repo.startswith("."):
                continue
            
            repo_path = os.path.join(base_path, repo)
            if not os.path.isdir(repo_path):
                continue
            
            for api_name in os.listdir(repo_path):
                if api_name.startswith("."):
                    continue
                
                api_path = os.path.join(repo_path, api_name)
                if not os.path.isdir(api_path):
                    continue
                
                versions = [
                    f.replace("v", "").replace(".md", "")
                    for f in os.listdir(api_path)
                    if f.startswith("v") and f.endswith(".md")
                ]
                
                if not versions:
                    continue
                
                latest_version = max([int(v) for v in versions], default=0)
                available_apis.append({
                    "repo": repo,
                    "api": api_name,
                    "version": latest_version
                })
    
    if not available_apis:
        return []
    
    # Step 2: Create context string for LLM
    api_list = "\n".join([
        f"- {api['repo']}/{api['api']} (v{api['version']})"
        for api in available_apis
    ])
    
    prompt = f"""You are an intelligent API search assistant. Given a user query, identify ALL relevant APIs from the following list.

AVAILABLE APIS:
{api_list}

USER QUERY: {query}

Return ONLY the matching APIs in this exact format (one per line):
repo_name/api_name

If no APIs match, respond with: NO_MATCHES

Instructions:
- Match APIs based on semantic meaning, functionality, and keywords
- Be inclusive in matching - if an API COULD be relevant to the query, include it
- Consider synonyms: "get" = "fetch" = "retrieve", "branches" = "branch", "details" = "info" = "information"
- Match partial concepts: if user asks for "all X" and you have an API about "X", include it
- Do NOT be overly strict - err on the side of inclusion"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )
        
        response.raise_for_status()
        result = response.json()
        response_text = result.get("response", "").strip()
        
        if not response_text or "NO_MATCHES" in response_text:
            return []
        
        # Step 3: Parse results
        matched = []
        for line in response_text.split("\n"):
            line = line.strip()
            if "/" not in line:
                continue
            
            try:
                repo, api_name = line.split("/", 1)
                repo = repo.strip()
                api_name = api_name.strip()
                
                # Find matching API in available list
                for available_api in available_apis:
                    if (available_api["repo"].lower() == repo.lower() and 
                        available_api["api"].lower() == api_name.lower()):
                        # Calculate confidence score based on query match
                        confidence = calculate_api_confidence(query, available_api)
                        available_api["confidence"] = confidence
                        matched.append(available_api)
                        break
            except ValueError:
                continue
        
        # Step 4: Fallback keyword matching if LLM found no results
        if not matched or "NO_MATCHES" in response_text:
            print(f"💡 LLM found no matches for '{query}', trying keyword matching...")
            query_lower = query.lower()
            
            # Extract keywords from query
            keywords = query_lower.split()
            
            for available_api in available_apis:
                api_name_lower = available_api["api"].lower()
                repo_lower = available_api["repo"].lower()
                
                # Check if any keywords appear in API name (case-insensitive)
                if any(keyword in api_name_lower or keyword in repo_lower for keyword in keywords):
                    if available_api not in matched:
                        confidence = calculate_api_confidence(query, available_api)
                        available_api["confidence"] = confidence
                        matched.append(available_api)
                        print(f"✓ Keyword matched: {available_api['repo']}/{available_api['api']} (confidence: {confidence:.2f})")
        
        # Sort by confidence score (highest first) and filter by minimum threshold
        MIN_CONFIDENCE = 0.3  # Only show APIs with 30%+ confidence
        matched = sorted(matched, key=lambda x: x.get("confidence", 0), reverse=True)
        matched = [api for api in matched if api.get("confidence", 0) >= MIN_CONFIDENCE]
        
        return matched
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Ollama not running for search. Start with: ollama serve")
        return []
    except requests.exceptions.Timeout:
        print("❌ Ollama search request timed out")
        return []
    except Exception as e:
        print(f"⚠️ Error searching APIs: {e}")
        return []


def answer_question_based_on_docs(query: str, matched_apis: List[Dict], base_path: str = DOCS_DIR) -> Dict:
    """
    Answer user questions based on actual API documentation.
    Acts as a KT provider/assistant using only available documentation.
    
    Args:
        query: User's natural language question
        matched_apis: List of matched APIs with repo, api, version info
        base_path: Base directory containing API documentation
        
    Returns:
        Dictionary with:
        - "answer": The LLM-generated answer based on docs
        - "success": Boolean indicating if answer was generated
        - "message": Error/info message if applicable
        
    Requirements:
        - Ollama running locally on http://localhost:11434
        - Mistral model pulled: ollama pull mistral
    """
    
    if not matched_apis:
        return {
            "success": False,
            "answer": None,
            "message": "I don't have any matching APIs for your query at this moment. Could you please rephrase or ask about something else?"
        }
    
    # Collect documentation from matched APIs
    docs_content = []
    for api in matched_apis:
        repo = api.get("repo")
        api_name = api.get("api")
        version = api.get("version")
        
        doc_path = os.path.join(base_path, repo, api_name, f"v{version}.md")
        
        try:
            if os.path.exists(doc_path):
                with open(doc_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content.strip():
                        docs_content.append({
                            "repo": repo,
                            "api": api_name,
                            "version": version,
                            "content": content[:2000]  # Limit to 2000 chars per doc
                        })
        except Exception as e:
            print(f"⚠️ Error reading {doc_path}: {e}")
            continue
    
    if not docs_content:
        return {
            "success": False,
            "answer": None,
            "message": "I don't have enough documentation available to answer your question at this moment. Please try again later or consult the API documentation directly."
        }
    
    # Build context from documentation
    context = "\n\n".join([
        f"API: {doc['repo']}/{doc['api']} (v{doc['version']})\n---\n{doc['content']}"
        for doc in docs_content
    ])
    
    # Create prompt for LLM
    prompt = f"""You are a helpful Knowledge Transfer (KT) provider and technical assistant. 
Based on the following API documentation, answer the user's question clearly and accurately.
Only use information from the provided documentation. 
If the documentation doesn't contain sufficient information to answer the question, say so clearly.
Be conversational but professional.

AVAILABLE API DOCUMENTATION:
{context}

USER QUESTION: {query}

INSTRUCTIONS:
- Answer based ONLY on the provided documentation
- If the documentation is insufficient, politely say: "I don't have enough information about that from the available documentation."
- Be helpful and provide relevant context from the docs
- Keep the answer concise but informative"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )
        
        response.raise_for_status()
        result = response.json()
        answer = result.get("response", "").strip()
        
        if answer:
            return {
                "success": True,
                "answer": answer,
                "message": None
            }
        else:
            return {
                "success": False,
                "answer": None,
                "message": "I couldn't generate an answer at this moment. Please try again."
            }
        
    except requests.exceptions.ConnectionError:
        print("❌ Ollama service is not available")
        return {
            "success": False,
            "answer": None,
            "message": "I'm experiencing technical difficulties right now. Our backend service is temporarily unavailable. Please try again in a moment."
        }
    except requests.exceptions.Timeout:
        print("❌ Ollama request timed out")
        return {
            "success": False,
            "answer": None,
            "message": "The request took too long to process. Please try again with a simpler question or try later."
        }
    except Exception as e:
        print(f"⚠️ Error generating answer: {e}")
        return {
            "success": False,
            "answer": None,
            "message": f"Something went wrong on our end. Please try again later."
        }
