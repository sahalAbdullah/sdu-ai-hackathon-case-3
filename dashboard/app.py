import streamlit as st

pg = st.navigation([
    st.Page("pages/1_Sales_Dashboard.py",  title="Sales Dashboard",  icon="📊"),
    st.Page("pages/2_Customer_Request.py", title="Customer Request",  icon="📋"),
    st.Page("pages/3_Order_Feasibility.py", title="Order Feasibility", icon="🏭"),
])
pg.run()
