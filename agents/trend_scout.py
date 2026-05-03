"""
Trend Scout Agent v2 — Full Bangalore Coverage
===============================================
Identifies what people search for around ANY Bangalore real estate topic:
localities, property types, legal, finance, lifestyle, infrastructure, comparisons.

Key improvements over v1:
- Auto-classifies topic type and generates context-aware queries
- Detects SERP features (featured snippets, AI overviews, knowledge panels)
- Discovers related localities and comparison opportunities
- Covers cross-cutting concerns (locality → legal/finance angles)
- Smarter SerpAPI budget allocation (prioritizes high-value queries)
- Kannada/local language query discovery
- AEO opportunity scoring (which queries have weak AI answers?)
"""

import json
import os
import re
import time
import hashlib
from serpapi import GoogleSearch
from config_loader import get_serpapi_key, cfg
from db.sqlite_ops import (
    cache_get, cache_set,
    start_agent_run, complete_agent_run, fail_agent_run,
)
from llm import call_llm_json

SERPAPI_KEY = get_serpapi_key()

# ─────────────────────────────────────────────────────────
# BANGALORE KNOWLEDGE (baked in — no API calls needed)
# ─────────────────────────────────────────────────────────

BANGALORE_LOCALITIES = [
    "MG Road", "Brigade Road", "Church Street", "Residency Road",
    "Richmond Town", "Shivajinagar", "Cunningham Road",
    "Lavelle Road", "St. Marks Road", "Cubbon Park",
    "UB City", "Vittal Mallya Road",

    "Jayanagar", "JP Nagar", "Banashankari", "Basavanagudi",
    "Uttarahalli", "Padmanabhanagar", "Kumaraswamy Layout",
    "Girinagar", "Chikkalasandra", "ISRO Layout",
    "Kanakapura Road", "Anjanapura", "Konanakunte",
    "Subramanyapura", "Talaghattapura", "Vajarahalli",
    "Arekere", "Bannerghatta Road", "Hulimavu",
    "Akshayanagar", "Begur", "Hongasandra", "Bommanahalli",

    "HSR Layout", "HSR Layout Sector 1", "HSR Layout Sector 2",
    "HSR Layout Sector 3", "HSR Layout Sector 4",
    "HSR Layout Sector 5", "HSR Layout Sector 6", "HSR Layout Sector 7",
    "Koramangala", "Koramangala 1st Block", "Koramangala 2nd Block",
    "Koramangala 3rd Block", "Koramangala 4th Block",
    "Koramangala 5th Block", "Koramangala 6th Block",
    "Koramangala 7th Block", "Koramangala 8th Block",
    "BTM Layout", "BTM 1st Stage", "BTM 2nd Stage",
    "Electronic City", "Electronic City Phase 1", "Electronic City Phase 2",
    "Sarjapur Road", "Sarjapur", "Harlur", "Haralur Road",
    "Bellandur", "Kasavanahalli", "Kaikondrahalli",
    "Agara", "Parappana Agrahara", "Bommasandra",
    "Chandapura", "Anekal", "Madiwala", "Silk Board",
    "Whitefield", "Whitefield Main Road", "ITPL",
    "Brookefield", "AECS Layout", "Marathahalli",
    "KR Puram", "Krishnarajapuram", "Mahadevapura",
    "Varthur", "Gunjur", "Balagere",
    "Kadugodi", "Hoodi", "Kundalahalli",
    "Thubarahalli", "Ramamurthy Nagar",
    "Kasturi Nagar", "CV Raman Nagar",
    "Indiranagar", "Domlur", "HAL", "Old Airport Road",

    "Hebbal", "Yelahanka", "New Yelahanka",
    "Thanisandra", "Thanisandra Main Road",
    "Hennur", "Hennur Road", "Kothanur",
    "Bagalur", "Jakkur", "Amrutahalli",
    "Sahakar Nagar", "Kodigehalli",
    "Vidyaranyapura", "Nagawara",
    "Devanahalli", "Kempegowda International Airport Road",
    "Chikkajala", "Doddaballapur Road",

    "Rajajinagar", "Malleswaram", "Yeshwanthpur",
    "Mathikere", "Mahalakshmi Layout",
    "Nandini Layout", "Basaveshwar Nagar",
    "Vijayanagar", "Attiguppe",
    "Nagarbhavi", "Kengeri", "Kengeri Satellite Town",
    "Magadi Road", "Sunkadakatte",

    "Frazer Town", "Cooke Town", "Ulsoor",
    "Benson Town", "Pulikeshi Nagar",
    "Tasker Town", "Richards Town", "Coles Park",

    "Gunjur Palya", "Dommasandra", "Mullur",
    "Chikkabellandur", "Kaggalipura",
    "Hosa Road", "Rayasandra",
    "Narayanapura", "Horamavu",
    "Seegehalli", "Bidarahalli",
    "Avalahalli", "Budigere Cross",
    "Old Madras Road", "TC Palya",
    "NRI Layout", "Medahalli",

    "Sadashivanagar", "RT Nagar",
    "Banashankari Stage 1", "Banashankari Stage 2",
    "Banashankari Stage 3", "Banashankari Stage 4",
    "JP Nagar Phase 1", "JP Nagar Phase 2",
    "JP Nagar Phase 3", "JP Nagar Phase 4",
    "JP Nagar Phase 5", "JP Nagar Phase 6",
    "JP Nagar Phase 7", "JP Nagar Phase 8",

    "North Bangalore", "South Bangalore", "East Bangalore", "West Bangalore"
]

BANGALORE_BUILDERS = [
    "Brigade", "Brigade Group", "Brigade Enterprises",
    "Prestige", "Prestige Group", "Prestige Estates",
    "Sobha", "Sobha Limited", "Sobha Developers",
    "Puravankara", "Purva", "Provident Housing",
    "Godrej Properties", "Godrej",
    "Embassy Group", "Embassy",
    "Salarpuria Sattva", "Sattva",
    "Mantri Developers", "Mantri",
    "Total Environment", "Total Environment Building Systems",
    "Adarsh Developers", "Adarsh",
    "Assetz Property Group", "Assetz",
    "Nitesh Estates",
    "Mahindra Lifespaces", "Mahindra",
    "Tata Housing", "Tata Realty",
    "DLF Bangalore", "DLF",
    "Lodha Bangalore", "Lodha",
    "Century Real Estate", "Century",
    "Shapoorji Pallonji", "SP Real Estate",
    "Hiranandani", "Hiranandani Communities",
    "Ozone Group", "Ozone",
    "RMZ", "RMZ Corp",
    "Bhartiya City",
    "Vaishnavi Group",
    "Concorde Group",
    "Confident Group",
    "Ranka Builders", "Ranka",
    "Pashmina",
    "House of Hiranandani",
    "Phoenix Mills", "Phoenix",
    "L&T Realty", "Larsen and Toubro",
]


# New category in TOPIC_CATEGORIES dict — add this entry:
# (You'll merge this into the existing TOPIC_CATEGORIES dict)
BUILDER_CATEGORY = {
    "builder": {
        "signals": [b.lower() for b in BANGALORE_BUILDERS],
        "description": "A Bangalore real estate developer/builder",
    },
}

TOPIC_CATEGORIES = {
    "locality": {
        "signals": [],  # Detected by matching against BANGALORE_LOCALITIES
        "description": "A specific Bangalore locality/area",
    },
    "property_type": {
        "signals": [
            "apartment", "villa", "plot", "flat", "penthouse", "duplex",
            "1bhk", "2bhk", "3bhk", "4bhk", "studio", "pg", "paying guest",
            "co-living", "independent house", "row house", "site",
            "commercial", "office space", "shop", "warehouse",
        ],
        "description": "A type of property or accommodation",
    },
    "legal": {
        "signals": [
            "rera", "registration", "stamp duty", "rental agreement",
            "sale deed", "khata", "ec", "encumbrance", "patta", "mutation",
            "property tax", "betterment charge", "conversion", "layout approval",
            "occupancy certificate", "completion certificate", "bda", "bbmp",
            "tenant", "landlord", "eviction", "notice period", "security deposit",
            "power of attorney", "will", "succession", "partition",
            "noc", "no objection", "title deed", "legal verification",
        ],
        "description": "Legal aspects of real estate",
    },
    "finance": {
        "signals": [
            "home loan", "emi", "interest rate", "down payment", "mortgage",
            "sbi home loan", "hdfc home loan", "lic housing", "bajaj housing",
            "loan eligibility", "pre-approved", "balance transfer",
            "capital gains", "tax benefit", "section 80c", "section 24",
            "stamp duty", "gst", "tds", "property tax", "cess",
            "investment", "roi", "rental yield", "appreciation",
            "affordable housing", "pmay", "pradhan mantri awas yojana",
            "budget", "price", "cost", "rate", "value",
        ],
        "description": "Financial aspects of real estate",
    },
    "lifestyle": {
        "signals": [
            "things to do", "restaurants", "cafes", "schools", "hospitals",
            "parks", "malls", "nightlife", "gyms", "supermarket", "grocery",
            "temple", "church", "mosque", "places to visit", "weekend",
            "food", "dining", "shopping", "entertainment", "recreation",
            "best", "top", "popular", "famous",
            "dog friendly", "pet friendly", "family friendly",
            "expat", "bachelor", "senior citizen", "retirement",
        ],
        "description": "Lifestyle, amenities, and things to do",
    },
    "infrastructure": {
        "signals": [
            "metro", "namma metro", "bmrcl", "bus", "bmtc", "airport",
            "railway", "ring road", "orr", "nice road", "peripheral ring road",
            "flyover", "underpass", "signal free corridor",
            "water supply", "bwssb", "bescom", "electricity", "power cut",
            "road", "pothole", "traffic", "commute", "connectivity",
            "upcoming", "under construction", "proposed", "masterplan",
            "smart city", "satellite town", "township",
        ],
        "description": "Infrastructure, connectivity, and development",
    },
    "process": {
        "signals": [
            "how to buy", "how to rent", "how to sell", "step by step",
            "guide", "checklist", "documents required", "process",
            "tips", "mistakes", "things to know", "before buying",
            "first time", "nri", "foreign national",
            "broker", "agent", "no broker", "direct owner",
            "negotiation", "site visit", "due diligence", "verification",
        ],
        "description": "Process guides and how-tos",
    },
    "market": {
        "signals": [
            "price trend", "market", "demand", "supply", "forecast",
            "2024", "2025", "2026", "outlook", "prediction",
            "bubble", "crash", "boom", "slowdown", "recovery",
            "new launch", "pre-launch", "under construction", "ready to move",
            "resale", "builder", "developer", "project",
            "inventory", "unsold", "absorption",
        ],
        "description": "Market trends and analysis",
    },
}

# Comparison patterns that people actually search for
COMPARISON_PATTERNS = [
    "{topic} vs {alt}",
    "{topic} or {alt} which is better",
    "{topic} compared to {alt}",
    "difference between {topic} and {alt}",
]

# Locality neighborhoods/alternatives (for cross-discovery)
LOCALITY_NEIGHBORS = {
    "hsr layout": ["btm layout", "koramangala", "bommanahalli", "bellandur", "sarjapur road"],
    "koramangala": ["indiranagar", "hsr layout", "ejipura", "domlur", "btm layout"],
    "indiranagar": ["koramangala", "domlur", "cv raman nagar", "old airport road", "frazer town"],
    "whitefield": ["marathahalli", "kr puram", "hoodi", "kadugodi", "varthur"],
    "electronic city": ["bommanahalli", "hosur road", "bannerghatta road", "sarjapur road", "hsr layout"],
    "marathahalli": ["whitefield", "bellandur", "hoodi", "old airport road", "kr puram"],
    "sarjapur road": ["bellandur", "hsr layout", "electronic city", "harlur", "varthur"],
    "hebbal": ["yelahanka", "thanisandra", "rt nagar", "hennur", "devanahalli"],
    "jp nagar": ["bannerghatta road", "jayanagar", "btm layout", "uttarahalli", "kanakapura road"],
    "jayanagar": ["jp nagar", "basavanagudi", "banashankari", "btm layout", "koramangala"],
    "bannerghatta road": ["jp nagar", "electronic city", "begur", "hulimavu", "arekere"],
    "yelahanka": ["hebbal", "devanahalli", "thanisandra", "jakkur", "bagalur"],
    "btm layout": ["hsr layout", "koramangala", "jp nagar", "bommanahalli", "jayanagar"],
    "bellandur": ["sarjapur road", "marathahalli", "hsr layout", "varthur", "harlur"],
    "rajajinagar": ["malleswaram", "vijayanagar", "basaveshwaranagar", "yeshwanthpur", "sadashivanagar"],
    "malleswaram": ["rajajinagar", "sadashivanagar", "yeshwanthpur", "seshadripuram", "frazer town"],
    "devanahalli": ["yelahanka", "bagalur", "north bangalore", "hebbal", "airport"],
    "thanisandra": ["hebbal", "hennur", "yelahanka", "nagawara", "jakkur"],
    "kanakapura road": ["jp nagar", "banashankari", "uttarahalli", "rajarajeshwari nagar", "mysore road"],
}


# ─────────────────────────────────────────────────────────
# TOPIC CLASSIFIER
# ─────────────────────────────────────────────────────────

def classify_topic(topic):
    """Now detects builders too."""
    topic_lower = topic.lower().strip()
    matches = []
    detected_localities = []
    detected_builders = []

    # Check builders FIRST 
    for builder in BANGALORE_BUILDERS:
        if builder.lower() in topic_lower:
            detected_builders.append(builder)
            if "builder" not in matches:
                matches.append("builder")

    # Then localities
    for loc in BANGALORE_LOCALITIES:
        if loc.lower() in topic_lower:
            detected_localities.append(loc)
            if "locality" not in matches:
                matches.append("locality")

    # Other categories
    for category, config in TOPIC_CATEGORIES.items():
        if category in ("locality", "builder"):
            continue
        for signal in config.get("signals", []):
            if signal in topic_lower:
                if category not in matches:
                    matches.append(category)
                break

    if not matches:
        words = topic.strip().split()
        if 1 <= len(words) <= 4 and words[0][0].isupper():
            matches.append("locality")
            detected_localities.append(topic.strip())
    if not matches:
        matches.append("market")

    return {
        "categories": matches,
        "primary_category": matches[0],
        "detected_localities": detected_localities,
        "detected_builders": detected_builders,
        "is_locality": "locality" in matches,
        "is_builder": "builder" in matches,
        "is_cross_cutting": len(matches) > 1,
    }


# ─────────────────────────────────────────────────────────
# QUERY GENERATORS (per topic type)
# ─────────────────────────────────────────────────────────
def _generate_queries_builder(topic, classification):
    """Generate search queries for a builder topic."""
    builder = classification["detected_builders"][0] if classification["detected_builders"] else topic
    queries = {
        "core": [
            f"{builder} Bangalore",
            f"{builder} projects Bangalore",
            f"{builder} reviews",
            f"{builder} group reviews",
            f"is {builder} a good builder",
        ],
        "projects": [
            f"{builder} ongoing projects",
            f"{builder} ready to move projects",
            f"{builder} new launch",
            f"{builder} pre launch",
            f"upcoming {builder} projects Bangalore",
        ],
        "reputation": [
            f"{builder} customer reviews",
            f"{builder} construction quality",
            f"{builder} delays",
            f"{builder} complaints",
            f"{builder} delivery track record",
        ],
        "comparison": [
            f"{builder} vs Sobha",
            f"{builder} vs Prestige",
            f"{builder} vs Brigade",
            f"best builders in Bangalore",
        ],
        "financial": [
            f"{builder} home loan approved banks",
            f"{builder} payment plans",
            f"{builder} price list",
        ],
        "legal": [
            f"{builder} RERA registration",
            f"{builder} OC certificate",
            f"{builder} project status RERA",
        ],
    }
    return queries


def _generate_queries_locality(topic, classification):
    """Generate search queries for a locality topic."""
    loc = classification["detected_localities"][0] if classification["detected_localities"] else topic

    queries = {
        "core": [
            f"{loc} Bangalore",
            f"{loc} Bangalore guide",
            f"living in {loc} Bangalore",
            f"{loc} review",
        ],
        "property": [
            f"{loc} property price",
            f"{loc} rent",
            f"2BHK rent {loc}",
            f"{loc} flat for sale",
            f"{loc} property rate per sq ft",
            f"{loc} new projects",
        ],
        "lifestyle": [
            f"things to do in {loc}",
            f"best restaurants {loc}",
            f"schools near {loc}",
            f"hospitals near {loc}",
        ],
        "connectivity": [
            f"{loc} metro station",
            f"{loc} to airport distance",
            f"{loc} connectivity",
            f"{loc} traffic",
        ],
        "practical": [
            f"{loc} pin code",
            f"{loc} BBMP ward",
            f"is {loc} safe",
            f"{loc} water supply",
            f"{loc} pros and cons",
        ],
        "comparison": [],
    }

    # Always ensure comparison queries exist
    loc_key = loc.lower()
    neighbors = LOCALITY_NEIGHBORS.get(loc_key, [])

    # Fallback neighbors if not in dict — generic well-known areas
    if not neighbors:
        neighbors = ["Whitefield", "Koramangala", "Indiranagar"]

    for neighbor in neighbors[:3]:
        for pattern in COMPARISON_PATTERNS:
            queries["comparison"].append(
                pattern.format(topic=loc, alt=neighbor)
            )

    return queries


def _generate_queries_property_type(topic, classification):
    """Generate search queries for a property type topic."""
    queries = {
        "core": [
            f"{topic} in Bangalore",
            f"{topic} Bangalore price",
            f"best {topic} in Bangalore",
        ],
        "by_locality": [
            f"{topic} in Whitefield",
            f"{topic} in Sarjapur Road",
            f"{topic} in Electronic City",
            f"{topic} in North Bangalore",
            f"{topic} in South Bangalore",
        ],
        "practical": [
            f"{topic} Bangalore under 50 lakhs",
            f"{topic} Bangalore for rent",
            f"affordable {topic} Bangalore",
            f"luxury {topic} Bangalore",
            f"new {topic} projects Bangalore",
        ],
        "comparison": [
            f"{topic} vs independent house Bangalore",
            f"buy vs rent {topic} Bangalore",
        ],
    }
    return queries


def _generate_queries_legal(topic, classification):
    """Generate search queries for a legal topic."""
    queries = {
        "core": [
            f"{topic} in Bangalore",
            f"{topic} in Karnataka",
            f"{topic} process",
            f"{topic} documents required",
        ],
        "how_to": [
            f"how to {topic}",
            f"{topic} step by step",
            f"{topic} online",
            f"{topic} fees",
            f"{topic} charges Bangalore",
        ],
        "specific": [
            f"{topic} 2026",
            f"{topic} rules Karnataka",
            f"{topic} format",
            f"{topic} checklist",
        ],
        "problems": [
            f"{topic} problems",
            f"{topic} mistakes to avoid",
            f"{topic} complaint",
            f"{topic} penalty",
        ],
    }
    return queries


def _generate_queries_finance(topic, classification):
    """Generate search queries for a finance topic."""
    queries = {
        "core": [
            f"{topic} Bangalore",
            f"{topic} in India 2026",
            f"{topic} calculator",
            f"best {topic}",
        ],
        "rates": [
            f"{topic} interest rate 2026",
            f"{topic} rate today",
            f"cheapest {topic}",
            f"{topic} comparison",
        ],
        "eligibility": [
            f"{topic} eligibility",
            f"{topic} for salaried",
            f"{topic} for self employed",
            f"{topic} documents required",
        ],
        "tax": [
            f"{topic} tax benefit",
            f"{topic} tax deduction",
            f"{topic} section 80c",
        ],
    }
    return queries


def _generate_queries_lifestyle(topic, classification):
    """Generate search queries for a lifestyle topic."""
    loc = ""
    if classification["detected_localities"]:
        loc = classification["detected_localities"][0]

    area = loc if loc else "Bangalore"
    queries = {
        "core": [
            f"{topic} {area}",
            f"best {topic} {area}",
            f"top {topic} {area}",
            f"{topic} near me {area}" if loc else f"{topic} Bangalore",
        ],
        "specific": [
            f"{topic} {area} 2026",
            f"{topic} {area} with reviews",
            f"affordable {topic} {area}",
            f"new {topic} {area}",
        ],
        "related": [
            f"{area} family friendly",
            f"{area} weekend activities",
            f"{area} for kids",
        ],
    }
    return queries


def _generate_queries_infrastructure(topic, classification):
    """Generate search queries for infrastructure."""
    queries = {
        "core": [
            f"{topic} Bangalore",
            f"{topic} Bangalore update",
            f"{topic} Bangalore 2026",
            f"{topic} status",
        ],
        "impact": [
            f"{topic} property prices",
            f"{topic} impact on real estate",
            f"areas near {topic}",
            f"best localities near {topic}",
        ],
        "timeline": [
            f"{topic} completion date",
            f"{topic} latest news",
            f"{topic} progress",
        ],
    }
    return queries


def _generate_queries_process(topic, classification):
    """Generate search queries for process/how-to topics."""
    queries = {
        "core": [
            f"{topic} in Bangalore",
            f"{topic} in Karnataka",
            f"{topic} step by step",
            f"{topic} guide",
        ],
        "specific": [
            f"{topic} documents required",
            f"{topic} fees",
            f"{topic} online",
            f"{topic} checklist",
            f"{topic} mistakes to avoid",
        ],
        "audience": [
            f"{topic} for first time buyer",
            f"{topic} for NRI",
            f"{topic} without broker",
        ],
    }
    return queries


def _generate_queries_market(topic, classification):
    """Generate search queries for market analysis topics."""
    queries = {
        "core": [
            f"{topic} Bangalore",
            f"Bangalore real estate {topic}",
            f"Bangalore property {topic}",
        ],
        "trends": [
            f"Bangalore property prices 2026",
            f"Bangalore real estate forecast",
            f"Bangalore housing market trend",
            f"should I buy property in Bangalore now",
        ],
        "data": [
            f"Bangalore property price index",
            f"Bangalore real estate report",
            f"Bangalore housing supply demand",
        ],
    }
    return queries


# Map categories to query generators
QUERY_GENERATORS = {
    "locality": _generate_queries_locality,
    "property_type": _generate_queries_property_type,
    "legal": _generate_queries_legal,
    "finance": _generate_queries_finance,
    "lifestyle": _generate_queries_lifestyle,
    "infrastructure": _generate_queries_infrastructure,
    "process": _generate_queries_process,
    "market": _generate_queries_market,
    "builder": _generate_queries_builder
}


def generate_all_queries(topic, classification, max_serp_calls=15):
    """
    Generate all search queries based on topic classification.
    Respects SerpAPI budget by prioritizing high-value queries.
    """
    primary = classification["primary_category"]
    generator = QUERY_GENERATORS.get(primary, _generate_queries_market)
    query_groups = generator(topic, classification)

    # If cross-cutting, pull core queries from secondary categories too
    if classification["is_cross_cutting"]:
        for secondary in classification["categories"][1:]:
            sec_generator = QUERY_GENERATORS.get(secondary)
            if sec_generator:
                sec_queries = sec_generator(topic, classification)
                core = sec_queries.get("core", [])[:2]
                if core:
                    query_groups[f"cross_{secondary}"] = core

    all_queries = []
    seen = set()

    priority_order = [
        "core",
        "property",
        "lifestyle",
        "comparison",
        "how_to",
        "rates",
        "practical",
        "specific",
        "connectivity",
        "by_locality",
        "impact",
        "related",
        "problems",
        "tax",
        "eligibility",
        "audience",
        "timeline",
        "data",
        "trends",
    ]

    for group in priority_order:
        for q in query_groups.get(group, []):
            q_normalized = q.lower().strip()
            if q_normalized not in seen:
                seen.add(q_normalized)
                all_queries.append({"query": q, "group": group})
                if len(all_queries) >= max_serp_calls:
                    break
        if len(all_queries) >= max_serp_calls:
            break

    # Catch any remaining groups not in priority_order
    for group, queries in query_groups.items():
        if len(all_queries) >= max_serp_calls:
            break
        for q in queries:
            q_normalized = q.lower().strip()
            if q_normalized not in seen:
                seen.add(q_normalized)
                all_queries.append({"query": q, "group": group})
                if len(all_queries) >= max_serp_calls:
                    break

    return all_queries


# ─────────────────────────────────────────────────────────
# SERP FEATURE DETECTION
# ─────────────────────────────────────────────────────────

def _search_serp_enhanced(query, num_results=10):
    """
    Enhanced SerpAPI search that also captures SERP features.
    """
    cache_key = f"serp_v2:{hashlib.md5(query.encode()).hexdigest()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        params = {
            "engine": "google",
            "q": query,
            "location": "Bangalore, Karnataka, India",
            "gl": "in",
            "hl": "en",
            "num": num_results,
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        results = search.get_dict()

        organic = [
            {
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "position": r.get("position", 0),
                "domain": r.get("link", "").split("/")[2] if r.get("link", "").startswith("http") else "",
            }
            for r in results.get("organic_results", [])
        ]

        paa = [
            {
                "question": q.get("question", ""),
                "snippet": q.get("snippet", ""),
                "link": q.get("link", ""),
            }
            for q in results.get("related_questions", [])
        ]

        related = [r.get("query", "") for r in results.get("related_searches", [])]

        serp_features = []
        if results.get("answer_box"):
            serp_features.append("featured_snippet")
        if results.get("knowledge_graph"):
            serp_features.append("knowledge_panel")
        if results.get("ai_overview"):
            serp_features.append("ai_overview")
        if results.get("local_results"):
            serp_features.append("local_pack")
        if results.get("inline_images"):
            serp_features.append("image_pack")
        if results.get("inline_videos") or any(
            "youtube.com" in r.get("link", "") for r in results.get("organic_results", [])
        ):
            serp_features.append("video_results")
        if results.get("shopping_results"):
            serp_features.append("shopping")
        if paa:
            serp_features.append("people_also_ask")

        featured_snippet = None
        if results.get("answer_box"):
            ab = results["answer_box"]
            featured_snippet = {
                "type": ab.get("type", ""),
                "title": ab.get("title", ""),
                "snippet": ab.get("snippet", ab.get("answer", "")),
                "source": ab.get("link", ""),
            }

        ai_overview = None
        if results.get("ai_overview"):
            aio = results["ai_overview"]
            ai_overview = {
                "text": aio.get("text", aio.get("snippet", "")),
                "sources": [s.get("link", "") for s in aio.get("sources", [])][:3],
            }

        competitor_domains = set()
        competitors = cfg.get("competitors", [])
        for r in organic:
            for comp in competitors:
                if comp in r.get("domain", ""):
                    competitor_domains.add(comp)

        data = {
            "query": query,
            "organic_results": organic,
            "people_also_ask": paa,
            "related_searches": related,
            "serp_features": serp_features,
            "featured_snippet": featured_snippet,
            "ai_overview": ai_overview,
            "competitor_presence": list(competitor_domains),
            "total_results": results.get("search_information", {}).get("total_results", 0),
        }

        cache_set(cache_key, data, ttl_days=7)
        time.sleep(1.2)
        return data

    except Exception as e:
        print(f"    SerpAPI error for '{query}': {e}")
        return {
            "query": query,
            "organic_results": [],
            "people_also_ask": [],
            "related_searches": [],
            "serp_features": [],
            "featured_snippet": None,
            "ai_overview": None,
            "competitor_presence": [],
            "total_results": 0,
        }


def _get_autocomplete_enhanced(seed):
    """Get Google autocomplete suggestions via SerpAPI with caching."""
    cache_key = f"autocomplete_v2:{hashlib.md5(seed.encode()).hexdigest()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        params = {
            "engine": "google_autocomplete",
            "q": seed,
            "gl": "in",
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        suggestions = [s.get("value", "") for s in results.get("suggestions", [])]
        cache_set(cache_key, suggestions, ttl_days=7)
        time.sleep(0.5)
        return suggestions
    except Exception as e:
        print(f"    Autocomplete error for '{seed}': {e}")
        return []


def _get_google_trends(topic):
    """
    Get Google Trends data via SerpAPI — no pytrends, no 429s, no proxies.
    Uses your existing SerpAPI key. Costs 2 SerpAPI credits per topic.
    """
    cache_key = f"trends_serpapi:{hashlib.md5(topic.lower().encode()).hexdigest()}"
    cached = cache_get(cache_key)
    if cached:
        print("    (trends from cache)")
        return cached

    try:
        kw = f"{topic} Bangalore"[:100]

        # ── Fetch interest over time ──
        params_time = {
            "engine": "google_trends",
            "q": kw,
            "geo": "IN-KA",
            "date": "today 12-m",
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params_time)
        time_data = search.get_dict()

        timeline = time_data.get("interest_over_time", {}).get("timeline_data", [])
        if not timeline:
            print("    Trends: no timeline data returned")
            return {
                "trend_available": False,
                "reason": "no_data",
                "fallback_strategy": "Using SERP data as proxy"
            }

        values = []
        for point in timeline:
            for val in point.get("values", []):
                extracted = val.get("extracted_value", 0)
                values.append(extracted)

        if not values:
            return {
                "trend_available": False,
                "reason": "no_values",
                "fallback_strategy": "Using SERP data as proxy"
            }

        avg = sum(values) / len(values)
        recent_avg = sum(values[-4:]) / 4 if len(values) >= 4 else avg
        peak = max(values)
        trough = min(values)

        if recent_avg > avg * 1.15:
            direction = "rising_fast"
        elif recent_avg > avg * 1.05:
            direction = "rising"
        elif recent_avg < avg * 0.85:
            direction = "declining_fast"
        elif recent_avg < avg * 0.95:
            direction = "declining"
        else:
            direction = "stable"

        is_seasonal = (peak - trough) > (avg * 0.5) if avg > 0 else False

        # ── Fetch related queries ──
        time.sleep(1.0)
        params_related = {
            "engine": "google_trends",
            "q": kw,
            "geo": "IN-KA",
            "data_type": "RELATED_QUERIES",
            "api_key": SERPAPI_KEY,
        }
        related_search = GoogleSearch(params_related)
        related_data = related_search.get_dict()

        rising_queries = [
            r.get("query", "")
            for r in related_data.get("related_queries", {}).get("rising", [])[:15]
        ]
        top_queries = [
            r.get("query", "")
            for r in related_data.get("related_queries", {}).get("top", [])[:10]
        ]

        result = {
            "trend_available": True,
            "average_interest": round(avg, 1),
            "recent_interest": round(recent_avg, 1),
            "peak_interest": peak,
            "direction": direction,
            "is_seasonal": is_seasonal,
            "rising_queries": rising_queries,
            "top_queries": top_queries,
        }

        cache_set(cache_key, result, ttl_days=1)
        time.sleep(1.0)
        return result

    except Exception as e:
        print(f"⚠️  SerpAPI Trends error: {e}")
        return {
            "trend_available": False,
            "error": str(e),
            "fallback_strategy": "Will use SERP related queries as proxy"
        }


def summarize_trends(trends_data):
    """Create human-readable trend summary for the LLM prompt."""
    if not trends_data or not trends_data.get("trend_available"):
        reason = trends_data.get("error", trends_data.get("reason", "unavailable"))
        return f"Trend data unavailable ({reason}). Using SERP signals as proxy."

    direction = trends_data.get("direction", "unknown")
    recent = trends_data.get("recent_interest", 0)
    avg = trends_data.get("average_interest", 0)
    seasonal = trends_data.get("is_seasonal", False)

    summary = f"Topic is {direction}."

    if recent > avg:
        summary += f" Recent interest ({recent}) up from 12-month average ({avg})."
    elif recent < avg:
        summary += f" Recent interest ({recent}) down from 12-month average ({avg})."
    else:
        summary += f" Interest stable around {avg}."

    if seasonal:
        summary += " Seasonal patterns detected."

    rising = trends_data.get("rising_queries", [])
    if rising:
        summary += f" {len(rising)} rising related queries found."

    return summary


def _extract_trend_proxies_from_serp(all_serp_results):
    """
    If Google Trends fails, extract trend signals from SERP data.
    Called AFTER SERP results are collected in run_trend_scout.
    """
    paa_question_count = 0
    pages_with_paa = 0
    assumed_rising_queries = []

    for result in all_serp_results:
        paa = result.get("people_also_ask", [])
        if paa:
            pages_with_paa += 1
            paa_question_count += len(paa)
            for q in paa[:3]:
                q_text = q.get("question", "") if isinstance(q, dict) else q
                if any(word in q_text.lower() for word in ["new", "latest", "upcoming", "2026", "stopped", "news"]):
                    assumed_rising_queries.append(q_text)

    # Heuristic: lots of PAA = topic in high demand / rising
    assumed_direction = "rising" if paa_question_count > 20 else "stable"

    return {
        "trend_available": False,
        "reason": "using_serp_proxy",
        "assumed_direction": assumed_direction,
        "serp_based_insights": {
            "questions_in_paa": paa_question_count,
            "assumed_rising_queries": assumed_rising_queries[:10],
            "pages_with_paa": pages_with_paa,
        },
        "fallback_strategy": "Derived from SERP People Also Ask signals"
    }


# ─────────────────────────────────────────────────────────
# AEO OPPORTUNITY SCORING
# ─────────────────────────────────────────────────────────

def _score_aeo_opportunity(serp_result):
    """
    Score how good an AEO opportunity a query is.
    Higher score = easier to become the AI-cited answer.
    """
    score = 50  # Base score

    query = serp_result.get("query", "").lower()

    # No featured snippet = opportunity
    if not serp_result.get("featured_snippet"):
        score += 15
    else:
        fs_source = serp_result.get("featured_snippet", {}).get("source", "")
        competitors = cfg.get("competitors", [])
        if any(c in fs_source for c in competitors):
            score += 5  # Competitor has it — we can displace them

    # No AI overview content = AEO opportunity
    if not serp_result.get("ai_overview"):
        score += 20

    # No competitor in top results
    if not serp_result.get("competitor_presence"):
        score += 10

    # Question-format queries have highest AEO potential
    if any(query.startswith(w) for w in ["what ", "how ", "why ", "when ", "where ", "is ", "can ", "should "]):
        score += 10

    # PAA present means Google sees this as Q&A territory
    if "people_also_ask" in serp_result.get("serp_features", []):
        score += 5

    return min(score, 100)


# ─────────────────────────────────────────────────────────
# LLM ANALYSIS PROMPT
# ─────────────────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = """You are a search trend analyst for Canvas Homes, a Bangalore real estate platform competing with MagicBricks and NoBroker.

Given search data for a topic, analyze and structure the findings into an actionable intelligence report.

CRITICAL: Your entire response must be a single valid JSON object.
No text before it, no text after it, no markdown fences, no explanation.
Start your response with { and end with }.

Your analysis must:
1. Identify the overall trend direction
2. Cluster queries by user intent (informational, transactional, navigational, comparative)
3. Identify content gaps , queries that return poor results, have no featured snippets, or lack AI overview answers
4. Identify AEO opportunities , queries where we can become the AI-cited source
5. Map queries to suggested content types (hub, spoke, sub-spoke, FAQ)
6. Flag any seasonal patterns
7. Identify the top 5 highest-priority queries to target first

Consider ALL query categories present: locality, property, legal, finance, lifestyle, infrastructure, process, market.

Respond with ONLY this JSON structure:
{
  "topic": "string",
  "topic_type": "locality|property_type|legal|finance|lifestyle|infrastructure|process|market",
  "trend_direction": "rising_fast|rising|stable|declining|declining_fast|unknown",
  "trend_summary": "1-2 sentence summary",
  "total_queries_discovered": 50,
  "intent_clusters": [
    {
      "intent": "informational|transactional|navigational|comparative",
      "queries": ["query1", "query2"],
      "estimated_volume": "high|medium|low",
      "aeo_opportunity": "high|medium|low"
    }
  ],
  "content_gaps": [
    {
      "gap": "Description of the gap",
      "queries": ["query1", "query2"],
      "opportunity": "What we should create",
      "suggested_content_type": "hub|spoke|sub_spoke|faq",
      "priority": "high|medium|low"
    }
  ],
  "aeo_targets": [
    {
      "query": "specific query",
      "current_answer_quality": "none|weak|moderate|strong",
      "our_strategy": "what we should write to win this",
      "content_type": "hub|spoke|faq"
    }
  ],
  "competitor_insights": [
    {
      "competitor": "domain",
      "strength": "what they do well for this topic",
      "weakness": "where we can beat them"
    }
  ],
  "top_5_priority_queries": [
    {
      "query": "string",
      "reason": "why this is high priority",
      "suggested_action": "what to create"
    }
  ],
  "related_topics_to_explore": ["topic1", "topic2"],
  "seasonal_notes": "any seasonal patterns detected, or null"
}"""


# ─────────────────────────────────────────────────────────
# MAIN AGENT
# ─────────────────────────────────────────────────────────

def run_trend_scout(seed_topic, cluster_id=None, max_serp_calls=15, max_autocomplete_calls=5):
    """
    Full Trend Scout analysis for any Bangalore real estate topic.
    """
    run_id = start_agent_run(
        "trend_scout", cluster_id=cluster_id,
        input_summary=f"Seed topic: {seed_topic} | Max SERP: {max_serp_calls}"
    )

    try:
        print(f"\n{'─'*60}")
        print(f"[Trend Scout v2] Analyzing: {seed_topic}")
        print(f"{'─'*60}")

        # ── Step 1: Classify the topic ──
        print("  [1/6] Classifying topic...")
        classification = classify_topic(seed_topic)
        print(f"    Type: {classification['primary_category']}")
        print(f"    Categories: {classification['categories']}")
        if classification["detected_localities"]:
            print(f"    Localities: {classification['detected_localities']}")

        # ── Step 2: Generate context-aware queries ──
        print(f"  [2/6] Generating queries (budget: {max_serp_calls} SERP calls)...")
        queries = generate_all_queries(seed_topic, classification, max_serp_calls)
        print(f"    Generated {len(queries)} unique queries across {len(set(q['group'] for q in queries))} groups")

        # ── Step 3: Google Trends (free, but rate-limited) ──
        print("  [3/6] Fetching Google Trends...")
        trends = _get_google_trends(seed_topic)
        if trends.get("trend_available"):
            print(f"    Direction: {trends['direction']}")
            print(f"    Rising queries: {len(trends.get('rising_queries', []))}")
        else:
            print(f"    Trends unavailable: {trends.get('error', trends.get('reason', 'unknown'))}")

        # ── Step 4: Run SERP searches ──
        print(f"  [4/6] Running {len(queries)} SERP searches...")
        all_serp_results = []
        all_paa = []
        all_related = []
        aeo_scores = []
        competitor_tracker = {}

        for i, q_info in enumerate(queries):
            query = q_info["query"]
            group = q_info["group"]
            print(f"    [{i+1}/{len(queries)}] ({group}) {query}")

            result = _search_serp_enhanced(query)
            all_serp_results.append(result)

            for paa in result.get("people_also_ask", []):
                q_text = paa.get("question", "")
                if q_text and q_text not in all_paa:
                    all_paa.append(q_text)

            for rel in result.get("related_searches", []):
                if rel and rel not in all_related:
                    all_related.append(rel)

            for comp in result.get("competitor_presence", []):
                competitor_tracker[comp] = competitor_tracker.get(comp, 0) + 1

            aeo_score = _score_aeo_opportunity(result)
            aeo_scores.append({
                "query": query,
                "score": aeo_score,
                "serp_features": result.get("serp_features", []),
                "has_featured_snippet": result.get("featured_snippet") is not None,
                "has_ai_overview": result.get("ai_overview") is not None,
                "competitor_present": bool(result.get("competitor_presence")),
            })

        print(f"    Total PAA questions: {len(all_paa)}")
        print(f"    Total related searches: {len(all_related)}")

        # ── After step 4: enrich trends with SERP proxy if trends unavailable ──
        if not trends.get("trend_available"):
            print("    Enriching trend data with SERP proxy signals...")
            trends = _extract_trend_proxies_from_serp(all_serp_results)

        # ── Step 5: Autocomplete ──
        print(f"  [5/6] Fetching autocomplete ({max_autocomplete_calls} calls)...")
        autocomplete_seeds = [seed_topic]
        if classification["is_locality"] and classification["detected_localities"]:
            loc = classification["detected_localities"][0]
            autocomplete_seeds.extend([
                f"{loc} Bangalore",
                f"rent in {loc}",
                f"{loc} property",
            ])
        else:
            autocomplete_seeds.extend([
                f"{seed_topic} Bangalore",
                f"{seed_topic} 2026",
                f"best {seed_topic}",
            ])

        all_autocomplete = []
        for seed in autocomplete_seeds[:max_autocomplete_calls]:
            suggestions = _get_autocomplete_enhanced(seed)
            for s in suggestions:
                if s not in all_autocomplete:
                    all_autocomplete.append(s)
        print(f"    Found {len(all_autocomplete)} autocomplete suggestions")

        # ── Step 6: LLM analysis ──
        print("  [6/6] Running LLM analysis...")

        aeo_scores.sort(key=lambda x: x["score"], reverse=True)
        top_aeo = aeo_scores[:10]

        # Build human-readable trend summary for the prompt
        trend_summary_text = summarize_trends(trends)

        analysis_input = f"""Analyze this search data for "{seed_topic}" in Bangalore.

TOPIC CLASSIFICATION:
- Primary type: {classification['primary_category']}
- All categories: {classification['categories']}
- Detected localities: {classification['detected_localities']}

GOOGLE TRENDS SUMMARY:
{trend_summary_text}

GOOGLE TRENDS RAW DATA:
{json.dumps(trends, indent=2)}

PEOPLE ALSO ASK ({len(all_paa)} unique questions):
{json.dumps(all_paa[:30], indent=2)}

RELATED SEARCHES ({len(all_related)} unique):
{json.dumps(all_related[:20], indent=2)}

AUTOCOMPLETE SUGGESTIONS ({len(all_autocomplete)} found):
{json.dumps(all_autocomplete[:20], indent=2)}

TOP AEO OPPORTUNITIES (scored 0-100, higher = easier to win):
{json.dumps(top_aeo, indent=2)}

COMPETITOR PRESENCE ACROSS SEARCHES:
{json.dumps(competitor_tracker, indent=2)}

SERP FEATURE SUMMARY:
- Queries with featured snippets: {sum(1 for s in aeo_scores if s['has_featured_snippet'])}
- Queries with AI overviews: {sum(1 for s in aeo_scores if s['has_ai_overview'])}
- Queries with competitor results: {sum(1 for s in aeo_scores if s['competitor_present'])}

TOP SERP RESULTS (first 3 per query, top 5 queries):
{json.dumps([
    {
        "query": r["query"],
        "top_3": [{"title": o["title"], "domain": o["domain"]} for o in r["organic_results"][:3]]
    }
    for r in all_serp_results[:5]
], indent=2)}

RISING QUERIES FROM GOOGLE TRENDS:
{json.dumps(trends.get('rising_queries', trends.get('serp_based_insights', {}).get('assumed_rising_queries', [])), indent=2)}

Provide a comprehensive analysis. Focus on AEO opportunities and content gaps.
"""

        result = call_llm_json(
            analysis_input,
            system=ANALYSIS_SYSTEM_PROMPT,
            model_role="bulk",
            max_tokens=4096,
        )
        analysis = result.get("parsed", {})

        # ── Package output ──
        total_queries_found = len(all_paa) + len(all_related) + len(all_autocomplete)
        if trends.get("rising_queries"):
            total_queries_found += len(trends["rising_queries"])

        output = {
            "topic": seed_topic,
            "classification": classification,
            "raw_data": {
                "trends": trends,
                "paa_questions": all_paa,
                "related_searches": all_related,
                "autocomplete": all_autocomplete,
                "serp_results_summary": [
                    {
                        "query": r["query"],
                        "top_results": r["organic_results"][:3],
                        "serp_features": r["serp_features"],
                        "competitor_presence": r["competitor_presence"],
                    }
                    for r in all_serp_results
                ],
                "aeo_scores": aeo_scores,
            },
            "analysis": analysis,
            "competitor_tracker": competitor_tracker,
            "serp_calls_used": len(queries),
            "autocomplete_calls_used": min(len(autocomplete_seeds), max_autocomplete_calls),
            "cost_usd": result.get("cost_usd", 0),
        }

        # ── Save and report ──
        complete_agent_run(
            run_id,
            output_summary=(
                f"Type: {classification['primary_category']} | "
                f"PAA: {len(all_paa)} | Related: {len(all_related)} | "
                f"Autocomplete: {len(all_autocomplete)} | "
                f"AEO targets: {len([s for s in aeo_scores if s['score'] >= 70])} high-opp | "
                f"SERP calls: {len(queries)}"
            ),
            tokens_in=result.get("tokens_in", 0),
            tokens_out=result.get("tokens_out", 0),
            cost_usd=result.get("cost_usd", 0),
        )

        print(f"\n  {'─'*50}")
        print(f"  ✅ Trend Scout v2 complete")
        print(f"     Topic type: {classification['primary_category']}")
        print(f"     PAA questions: {len(all_paa)}")
        print(f"     Related searches: {len(all_related)}")
        print(f"     Autocomplete: {len(all_autocomplete)}")
        print(f"     High AEO opportunities: {len([s for s in aeo_scores if s['score'] >= 70])}")
        print(f"     Competitor coverage: {json.dumps(competitor_tracker)}")
        print(f"     SERP calls used: {len(queries)}/{max_serp_calls}")
        print(f"     Cost: ${result.get('cost_usd', 0):.4f}")
        print(f"  {'─'*50}")

        return output

    except Exception as e:
        fail_agent_run(run_id, str(e))
        print(f"  ❌ Trend Scout failed: {e}")
        raise


# ─────────────────────────────────────────────────────────
# CLI INTERFACE
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m agents.trend_scout <topic> [--max-serp N] [--classify-only]")
        print("")
        print("Examples:")
        print('  python -m agents.trend_scout "HSR Layout"')
        print('  python -m agents.trend_scout "home loan"')
        print('  python -m agents.trend_scout "RERA registration"')
        print('  python -m agents.trend_scout "best schools Whitefield"')
        print('  python -m agents.trend_scout "2BHK rent Koramangala"')
        print('  python -m agents.trend_scout "Namma Metro Phase 3"')
        print('  python -m agents.trend_scout "Bangalore property market 2026"')
        print('  python -m agents.trend_scout "HSR Layout" --classify-only')
        print('  python -m agents.trend_scout "HSR Layout" --max-serp 5')
        sys.exit(0)

    topic = sys.argv[1]

    # Parse flags
    classify_only = "--classify-only" in sys.argv
    max_serp = 15
    if "--max-serp" in sys.argv:
        idx = sys.argv.index("--max-serp")
        if idx + 1 < len(sys.argv):
            max_serp = int(sys.argv[idx + 1])

    if classify_only:
        classification = classify_topic(topic)
        print(json.dumps(classification, indent=2))
        queries = generate_all_queries(topic, classification, max_serp)
        print(f"\nGenerated {len(queries)} queries:")
        for q in queries:
            print(f"  [{q['group']}] {q['query']}")
    else:
        result = run_trend_scout(topic, max_serp_calls=max_serp)

        # Safe filename — strip special chars, cap length
        safe_topic = re.sub(r'[^\w\s-]', '', topic.lower()).strip().replace(' ', '_')[:60]
        output_path = f"outputs/trend_scout_{safe_topic}.json"
        os.makedirs("outputs", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nFull output saved to: {output_path}")

        # Print analysis summary
        print("\n" + "="*60)
        print("ANALYSIS SUMMARY")
        print("="*60)
        print(json.dumps(result.get("analysis", {}), indent=2))