import streamlit as st
import sqlite3
import pandas as pd
import json
from config_loader import get_path

DB_PATH = get_path("database")


# ----------------------------
# DB CONNECTION
# ----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    return conn


# ----------------------------
# GET TABLES
# ----------------------------
def get_tables():
    conn = get_conn()
    tables = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table';", conn
    )
    conn.close()
    return tables["name"].tolist()


# ----------------------------
# GET TABLE DATA
# ----------------------------
def get_table_data(table):
    conn = get_conn()
    df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    conn.close()
    return df


# ----------------------------
# GET SCHEMA
# ----------------------------
def get_schema(table):
    conn = get_conn()
    schema = pd.read_sql_query(f"PRAGMA table_info({table});", conn)
    conn.close()
    return schema


# ----------------------------
# DELETE ROW
# ----------------------------
def delete_row(table, row_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
    conn.commit()
    conn.close()


# ----------------------------
# MAIN APP
# ----------------------------
def main():
    st.set_page_config(layout="wide")
    st.title("🧠 SQLite DB Viewer")

    tables = get_tables()

    # Sidebar
    selected_table = st.sidebar.selectbox("Select Table", tables)

    st.header(f"📦 Table: {selected_table}")

    # Schema
    with st.expander("📐 Table Schema"):
        schema = get_schema(selected_table)
        st.dataframe(schema, use_container_width=True)

    # Load data
    df = get_table_data(selected_table)

    if df.empty:
        st.warning("No data in this table.")
        return

    # ----------------------------
    # SEARCH
    # ----------------------------
    search = st.text_input("🔍 Search")

    if search:
        df = df[df.astype(str).apply(
            lambda row: row.str.contains(search, case=False).any(), axis=1
        )]

    # ----------------------------
    # PAGINATION
    # ----------------------------
    page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=1)
    page_number = st.number_input("Page", min_value=1, value=1)

    start = (page_number - 1) * page_size
    end = start + page_size

    st.dataframe(df.iloc[start:end], use_container_width=True)

    # ----------------------------
    # ROW INSPECTOR
    # ----------------------------
    st.subheader("🔍 Row Inspector")

    if "id" in df.columns:
        selected_id = st.selectbox("Select Row ID", df["id"].astype(str))

        row = df[df["id"].astype(str) == selected_id].iloc[0].to_dict()

        for key, value in row.items():
            st.markdown(f"**{key}**")

            # Pretty print JSON
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
                st.json(parsed)
            except:
                st.write(value)

        # ----------------------------
        # DELETE BUTTON
        # ----------------------------
        if st.button("🗑️ Delete Row"):
            delete_row(selected_table, selected_id)
            st.success("Row deleted. Refreshing...")
            st.experimental_rerun()

    # ----------------------------
    # REFRESH
    # ----------------------------
    if st.button("🔄 Refresh"):
        st.experimental_rerun()


if __name__ == "__main__":
    main()