from pathlib import Path
import streamlit as st

ROOT = Path(__file__).parent.parent
st.logo(str(ROOT / "images" / "image.png"), size="large")
st.markdown("""
<style>
[data-testid="stLogo"] { height: 80px !important; }
[data-testid="stLogo"] img { max-height: 100px !important; width: auto !important; }
</style>
""", unsafe_allow_html=True)

pg = st.navigation([
    st.Page("pages/1_Sales_Dashboard.py",  title="Sales Dashboard",  icon="📊"),
    st.Page("pages/2_Customer_Request.py", title="Customer Request",  icon="📋"),
    st.Page("pages/3_Order_Feasibility.py", title="Order Feasibility", icon="🏭"),
])
pg.run()
