import os
import requests
from datetime import datetime, timezone
from uuid import uuid4
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    chat_protocol_spec, ChatMessage, ChatAcknowledgement, TextContent
)

# --- 1. AGENT INITIALIZATION ---
agent = Agent()

# Initialize Tavily API Key
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "tvly-YOUR_API_KEY")
TAVILY_API_URL = "https://api.tavily.com/search"

# --- 2. RESEARCH & ANALYSIS FUNCTIONS ---
def evidence_researcher(claim: str):
    """
    Uses Tavily REST API to search news and government sources.
    """
    try:
        news_payload = {
            "api_key": TAVILY_API_KEY,
            "query": claim,
            "topic": "news",
            "max_results": 2
        }
        news_response = requests.post(TAVILY_API_URL, json=news_payload).json()
        
        govt_payload = {
            "api_key": TAVILY_API_KEY,
            "query": f"{claim} site:.gov OR official press release",
            "search_depth": "advanced",
            "max_results": 1
        }
        govt_response = requests.post(TAVILY_API_URL, json=govt_payload).json()
        
        return news_response.get("results", []) + govt_response.get("results", [])
    except Exception as e:
        return []

def fact_check_researcher(claim: str):
    """
    Dedicated search for fact-checking websites.
    """
    try:
        fc_payload = {
            "api_key": TAVILY_API_KEY,
            "query": f"{claim} fact check (reuters OR ap news OR politifact OR snopes)",
            "search_depth": "basic",
            "max_results": 2
        }
        response = requests.post(TAVILY_API_URL, json=fc_payload).json()
        return response.get("results", [])
    except Exception as e:
        return []

def verdict_analyst(claim, evidence_results, fc_results):
    """
    Analyzes the content and explicitly checks for relevance to avoid hallucinating verdicts.
    """
    all_context = " ".join([res.get('content', '') for res in (evidence_results + fc_results)]).lower()
    
    # 1. No results found at all
    if not all_context.strip():
        return "Unverified", "I couldn't find any reliable information or sources online to verify this claim."
        
    # 2. Relevance Check: Prevent false verdicts from unrelated search results
    # Extract meaningful keywords from the claim (longer than 3 letters, ignoring common filler words)
    ignore_words = {"there", "that", "this", "from", "news", "true", "false", "friend", "with", "about"}
    claim_words = [w.lower() for w in claim.split() if len(w) > 3 and w.lower() not in ignore_words]
    
    # Count how many key claim words actually appear in the search results
    match_count = sum(1 for word in claim_words if word in all_context)
    
    # If fewer than 40% of the significant words match, the results are likely unrelated
    if claim_words and (match_count / len(claim_words)) < 0.4:
        return "Unverified", "I searched online, but could not find any specific, credible information about this exact claim."

    # 3. Keyword Heuristics (Only applied if the text is proven to be relevant)
    if any(word in all_context for word in ["false", "hoax", "debunked", "fake", "fabricated"]):
        return "Fake", "Fact-checkers or reliable sources indicate this claim is false or a hoax."
         
    if any(word in all_context for word in ["misleading", "partially", "partly", "exaggerated"]):
        return "Partially Real", "The claim contains some truth but is highly misleading, exaggerated, or lacks key context."

    if any(word in all_context for word in ["true", "confirmed", "official", "arrested", "happened", "survived", "occurred"]):
        return "Real", "News and official sources confirm this event took place or is true."

    # Fallback if no specific verdict keywords are found
    return "Unverified", "I found some related information, but it is not definitive enough to confirm if the claim is true or false."


# --- 3. ASI:ONE CHAT PROTOCOL COORDINATOR ---
chat_proto = Protocol(spec=chat_protocol_spec)

def create_text_chat(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)]
    )

@chat_proto.on_message(model=ChatMessage)
async def verify_claim_handler(ctx: Context, sender: str, msg: ChatMessage):
    # Acknowledge immediately
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))

    claim = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            claim = item.text
            
    if not claim:
        return

    ctx.logger.info(f"📱 ASI:ONE ASKED TO CHECK: '{claim}'")

    # Fetch raw data dicts
    evidence_results = evidence_researcher(claim)
    fc_results = fact_check_researcher(claim)
    
    # Get concise verdict
    verdict, explanation = verdict_analyst(claim, evidence_results, fc_results)

    # Build concise response
    response_text = f"**Verdict:** {verdict}\n**Explanation:** {explanation}"
    
    # Only show sources if we actually found relevant evidence 
    # (Prevents showing random links when it defaults to Unverified)
    if verdict != "Unverified":
        all_urls = list(set([res.get("url") for res in (evidence_results + fc_results) if res.get("url")]))
        if all_urls:
            url_list = "\n".join([f"- {url}" for url in all_urls[:3]])
            response_text += f"\n\n**Sources:**\n{url_list}"

    ctx.logger.info(f"✅ FINAL VERDICT: {verdict}")
    
    # Send short, concise response
    await ctx.send(sender, create_text_chat(response_text))

@chat_proto.on_message(model=ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass

# --- 4. PUBLISH MANIFEST ---
agent.include(chat_proto, publish_manifest=True)