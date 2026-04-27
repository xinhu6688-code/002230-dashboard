import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ================== 1. 网页配置 & 手机端样式优化 ==================
st.set_page_config(page_title="讯飞策略-决策版", layout="wide")

st.markdown("""
    <style>
    .main .block-container {padding-top: 1rem; padding-left: 0.5rem; padding-right: 0.5rem;}
    [data-testid="stMetricValue"] {font-size: 1.1rem !important; font-weight: bold;}
    .modebar{display: none !important;}
    </style>
    """, unsafe_allow_html=True)

SYMBOL_NAME = "科大讯飞"
SYMBOL_CODE = "002230" 

# ================== 2. 核心数据引擎 ==================

def get_sina_snapshot():
    """仅获取最新价格，用于实时更新指标"""
    url = f"https://hq.sinajs.cn/list=sz{SYMBOL_CODE}"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        raw = r.text.split('"')[1].split(',')
        if len(raw) > 30:
            return {'date': pd.to_datetime(raw[30]), 'close': float(raw[3])}
    except: return None

@st.cache_data(ttl=60)
def get_historical_data():
    bs.login()
    try:
        start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(f"sz.{SYMBOL_CODE}", "date,open,high,low,close",
                                        start_date=start, frequency="d", adjustflag="2")
        data = []
        while rs.error_code == '0' and rs.next():
            data.append(rs.get_row_data())
        df = pd.DataFrame(data, columns=['date','open','high','low','close'])
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df.set_index('date', inplace=True)
        return df.sort_index()
    finally:
        bs.logout()

# ================== 3. 策略逻辑 ==================

df_raw = get_historical_data()
if not df_raw.empty:
    df = df_raw.copy()
    
    # 拼入实时价：虽然不显分时，但最新的均线计算需要这个实时价格
    snap = get_sina_snapshot()
    if snap and snap['date'].date() not in df.index:
        df.loc[snap['date'].date()] = [snap['close']]*4 
    
    # 指标全集
    df['ma9'] = df['close'].rolling(9).mean()
    df['ma25'] = df['close'].rolling(25).mean()
    df['ma90'] = df['close'].rolling(90).mean()
    
    # HV60 分位
    log_ret = np.log(df['close'] / df['close'].shift(1))
    vol = log_ret.rolling(60).std() * np.sqrt(252)
    df['hv_pctile'] = vol.rolling(250).apply(lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True)
    
    latest = df.iloc[-1]

    # ================== 4. 界面渲染 ==================
    
    st.title(f"📊 {SYMBOL_NAME} ({SYMBOL_CODE})")

    # --- 第一排：价格与核心波动 ---
    c1, c2, c3 = st.columns(3)
    c1.metric("当前实时价", f"¥{latest['close']:.2f}")
    c2.metric("HV60分位占比", f"{latest['hv_pctile']:.1%}")
    c3.metric("MA9 (紫线)", f"{latest['ma9']:.2f}")
    
    # --- 第二排：长期防御/支撑位 ---
    c4, c5 = st.columns(2)
    c4.metric("MA25 (黄线支撑)", f"{latest['ma25']:.2f}")
    c5.metric("MA90 (红线牛熊)", f"{latest['ma90']:.2f}")

    # --- 第三排：趋势图 (锁定缩放，防止手机误点) ---
    st.write("---")
    st.subheader("📅 历史走势与波动分析")
    pdf = df.tail(120)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
    
    # 主图：K线簇
    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='purple', width=1.5), name="MA9"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma25'], line=dict(color='#FFD700', width=1.5), name="MA25"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma90'], line=dict(color='red', width=1.5), name="MA90"), row=1, col=1)
    
    # 副图：HV60分位图
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['hv_pctile'], fill='tozeroy', line=dict(color='orange'), name="HV分位"), row=2, col=1)
    
    fig.update_layout(
        height=600, 
        xaxis_rangeslider_visible=False, 
        template="plotly_white", 
        dragmode=False, # 锁定，手指滑动不会导致图表变形
        margin=dict(l=0,r=0,t=0,b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    # 锁定坐标轴
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # --- 第四排：明细表格 ---
    st.write("---")
    st.subheader("📋 指标数值明细 (近15日)")
    table_df = df[['close', 'ma9', 'ma25', 'ma90', 'hv_pctile']].tail(15).iloc[::-1]
    st.dataframe(
        table_df.style.format({'close': '{:.2f}', 'ma9': '{:.2f}', 'ma25': '{:.2f}', 'ma90': '{:.2f}', 'hv_pctile': '{:.1%}'}), 
        use_container_width=True
    )

else:
    st.error("数据加载失败，请检查 Baostock 状态。")