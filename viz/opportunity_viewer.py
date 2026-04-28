"""
Streamlit component: 2-axis opportunity scatter plot.

Three tabs:
  1. Articles only (from Content Architect output)
  2. FAQs only (from FAQ Architect output)
  3. Combined view

Drop into your dashboard.py with:
    from dashboard_components.opportunity_view import render_opportunity_view
    render_opportunity_view(view_run_id, m)  # m = your loaded modules dict
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from viz.opportunity_matrix import build_opportunity_matrix


def _scatter_figure(dots, color_by="type", title=""):
    """Build a Plotly scatter for one set of dots."""
    if not dots:
        return None
    df = pd.DataFrame(dots)

    color_map = {
        "article": "#00d4ff",
        "faq": "#c8ff00",
        "hub": "#ff6b6b",
        "spoke": "#5a9fff",
        "sub_spoke": "#aa88ff",
    }

    # Color by article_type if present, else by type
    if "article_type" in df.columns and df["article_type"].notna().any():
        df["color_key"] = df["article_type"].fillna(df["type"])
    else:
        df["color_key"] = df["type"]

    fig = go.Figure()

    for key, group in df.groupby("color_key"):
        fig.add_trace(go.Scatter(
            x=group["x"],
            y=group["y"],
            mode="markers",
            marker=dict(
                size=group["size_visual"],
                color=color_map.get(key, "#888"),
                line=dict(width=1, color="rgba(0,0,0,0.3)"),
                sizemode="diameter",
            ),
            text=group["label"],
            customdata=group[["query", "size"]].values,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "Query: %{customdata[0]}<br>"
                "Volume: %{x}<br>"
                "Ease: %{y}<br>"
                "AEO Score: %{customdata[1]}<extra></extra>"
            ),
            name=str(key),
        ))

    # Add quadrant lines + labels
    fig.add_shape(type="line", x0=50, y0=0, x1=50, y1=100,
                  line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dash"))
    fig.add_shape(type="line", x0=0, y0=50, x1=100, y1=50,
                  line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dash"))

    fig.add_annotation(x=85, y=95, text="🎯 GO NOW", showarrow=False,
                       font=dict(color="#00ff88", size=11))
    fig.add_annotation(x=15, y=95, text="🌱 Quick wins", showarrow=False,
                       font=dict(color="#aacc00", size=11))
    fig.add_annotation(x=85, y=15, text="🏔 Strategic", showarrow=False,
                       font=dict(color="#ffcc44", size=11))
    fig.add_annotation(x=15, y=15, text="❌ Skip", showarrow=False,
                       font=dict(color="#ff6b6b", size=11))

    fig.update_layout(
        title=title,
        xaxis=dict(title="Search Volume Score (0-100)", range=[0, 105], gridcolor="#222"),
        yaxis=dict(title="Ease Score (100 = no competition)", range=[0, 105], gridcolor="#222"),
        plot_bgcolor="#0f0f1a",
        paper_bgcolor="#0f0f1a",
        font=dict(color="#aaa"),
        height=550,
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#aaa")),
    )
    return fig


def render_opportunity_view(view_run_id, m):
    """
    Render the full 3-tab opportunity view.
    `m` is the loaded modules dict from dashboard.py (must contain load_agent_output).
    """
    st.markdown("### 📊 Opportunity Matrix")
    st.caption(
        "Plots every planned article and every FAQ on a 2-axis grid. "
        "X = search volume proxy, Y = ease (low competition), dot size = AEO opportunity score."
    )

    trend_data = m["load_agent_output"](view_run_id, "trend_scout")
    cluster_plan_out = m["load_agent_output"](view_run_id, "content_architect")
    faq_plan_out = m["load_agent_output"](view_run_id, "faq_architect")

    if not trend_data:
        st.info("Run Trend Scout first — opportunity matrix needs trend + competitor data.")
        return

    cluster_plan = (cluster_plan_out or {}).get("cluster_plan") if cluster_plan_out else None
    faq_plan = faq_plan_out  # already in flat form

    matrix = build_opportunity_matrix(trend_data, cluster_plan, faq_plan)

    # Quadrant guide
    with st.expander("🧭 How to read this chart"):
        guide = matrix["axis_meta"].get("quadrant_guide", {})
        for q, label in guide.items():
            st.markdown(f"- **{q.replace('_', ' ').title()}:** {label}")
        st.caption("Dot size uses sqrt scaling so visual *area* is proportional to AEO score.")

    tab1, tab2, tab3 = st.tabs(["Articles", "FAQs", "Combined"])

    with tab1:
        if not matrix["articles"]:
            st.info("Content Architect hasn't run yet — no articles to plot.")
        else:
            fig = _scatter_figure(matrix["articles"],
                                  title=f"{len(matrix['articles'])} planned articles")
            st.plotly_chart(fig, use_container_width=True)

            # Sortable table
            df = pd.DataFrame(matrix["articles"])[
                ["label", "x", "y", "size", "article_type", "slug"]
            ].rename(columns={
                "label": "Title", "x": "Volume", "y": "Ease",
                "size": "AEO", "article_type": "Type", "slug": "Slug",
            })
            st.dataframe(df.sort_values(["AEO", "Volume"], ascending=False),
                        use_container_width=True, hide_index=True)

    with tab2:
        if not matrix["faqs"]:
            st.info("FAQ Architect hasn't run yet — no FAQs to plot.")
        else:
            fig = _scatter_figure(matrix["faqs"],
                                  title=f"{len(matrix['faqs'])} planned FAQs")
            st.plotly_chart(fig, use_container_width=True)

            df = pd.DataFrame(matrix["faqs"])[
                ["label", "x", "y", "size", "intent", "parent_slug"]
            ].rename(columns={
                "label": "Question", "x": "Volume", "y": "Ease",
                "size": "AEO", "intent": "Intent", "parent_slug": "Article",
            })
            st.dataframe(df.sort_values(["AEO", "Volume"], ascending=False),
                        use_container_width=True, hide_index=True)

    with tab3:
        combined = matrix["articles"] + matrix["faqs"]
        if not combined:
            st.info("Run Content + FAQ Architect to see the combined view.")
        else:
            fig = _scatter_figure(combined,
                                  title=f"{len(combined)} total content opportunities")
            st.plotly_chart(fig, use_container_width=True)

            # Quadrant breakdown
            top_right = [d for d in combined if d["x"] >= 50 and d["y"] >= 50]
            top_left = [d for d in combined if d["x"] < 50 and d["y"] >= 50]
            bot_right = [d for d in combined if d["x"] >= 50 and d["y"] < 50]
            bot_left = [d for d in combined if d["x"] < 50 and d["y"] < 50]

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("🎯 GO NOW", len(top_right))
            c2.metric("🌱 Quick wins", len(top_left))
            c3.metric("🏔 Strategic", len(bot_right))
            c4.metric("❌ Skip", len(bot_left))