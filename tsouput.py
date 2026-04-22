import streamlit as st
import json
import pandas as pd

st.set_page_config(page_title="Local SEO Analyzer", layout="wide")

st.title("📊 Local SEO Intelligence Dashboard")

# -------- FILE UPLOAD --------
uploaded_file = st.file_uploader("Upload JSON Output", type=["json"])

if uploaded_file:
    data = json.load(uploaded_file)

    # -------- BASIC INFO --------
    st.header("📍 Topic Overview")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("Topic", data.get("topic", "N/A"))
        st.metric("Primary Category", data["classification"].get("primary_category", "N/A"))

    with col2:
        st.metric("Is Locality", data["classification"].get("is_locality", False))
        st.metric("Detected Localities", ", ".join(data["classification"].get("detected_localities", [])))

    # -------- PAA QUESTIONS --------
    st.header("❓ People Also Ask (User Intent)")

    paa = data["raw_data"].get("paa_questions", [])
    if paa:
        for q in paa:
            st.write(f"- {q}")
    else:
        st.warning("No PAA data found")

    # -------- RELATED SEARCHES --------
    st.header("🔍 Related Searches")

    related = data["raw_data"].get("related_searches", [])
    if related:
        st.write(", ".join(related))
    else:
        st.warning("No related searches found")

    # -------- AEO OPPORTUNITIES --------
    st.header("🏆 AEO Opportunities (Best Targets)")

    aeo = data["raw_data"].get("aeo_scores", [])
    if aeo:
        df_aeo = pd.DataFrame(aeo)
        df_aeo = df_aeo.sort_values(by="score", ascending=False)

        st.dataframe(df_aeo, use_container_width=True)

        st.subheader("🔥 Top Opportunities")
        top = df_aeo[df_aeo["score"] >= 90]

        for _, row in top.iterrows():
            st.success(f"{row['query']} (Score: {row['score']})")

    else:
        st.warning("No AEO data available")

    # -------- SERP SUMMARY --------
    st.header("📊 SERP Insights")

    serp = data["raw_data"].get("serp_results_summary", [])

    if serp:
        for entry in serp:
            st.subheader(f"Query: {entry['query']}")

            for res in entry.get("top_results", []):
                st.write(f"🔗 {res['title']}")
                st.caption(res["snippet"])

            st.write("**SERP Features:**", ", ".join(entry.get("serp_features", [])))
            st.write("---")
    else:
        st.warning("No SERP data available")

    # -------- COMPETITOR TRACKER --------
    st.header("🏢 Competitor Dominance")

    competitors = data.get("competitor_tracker", {})

    if competitors:
        df_comp = pd.DataFrame(
            list(competitors.items()), columns=["Competitor", "Appearances"]
        ).sort_values(by="Appearances", ascending=False)

        st.bar_chart(df_comp.set_index("Competitor"))
        st.dataframe(df_comp, use_container_width=True)

    else:
        st.warning("No competitor data found")

    # -------- SYSTEM HEALTH --------
    st.header("⚙️ System Health")

    trends = data["raw_data"].get("trends", {})

    if not trends.get("trend_available", True):
        st.error(f"Trend Data Missing: {trends.get('error')}")

    analysis = data.get("analysis", {})

    if not analysis.get("intent_clusters"):
        st.warning("⚠️ Insight layer incomplete (LLM parsing failed)")

else:
    st.info("Upload a JSON file to begin")