import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ================== 1. 网页配置 ==================
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

# ================== 2. 数据引擎 ==================

def get_sina_snapshot():
    url = f"https://hq.sinajs.cn/list=sz{SYMBOL_CODE}"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        raw = r.text.split('"')[1].split(',')
        if len(raw) > 30:
            return {'date': pd.to_datetime(raw[30]), 'close': float(raw[3]), 'high': float(raw[4]), 'low': float(raw[5])}
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

# ================== 3. 指标计算 (找回 ATR) ==================

df_raw = get_historical_data()
if not df_raw.empty:
    df = df_raw.copy()
    
    snap = get_sina_snapshot()
    if snap and snap['date'].date() not in df.index:
        df.loc[snap['date'].date()] = [snap['close']]*4 
    
    # 1. 均线
    df['ma9'] = df['close'].rolling(9).mean()
    df['ma25'] = df['close'].rolling(25).mean()
    df['ma90'] = df['close'].rolling(90).mean()
    
    # 2. ATR (找回此处逻辑)
    pc = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(25).mean()
    
    # 3. HV60 分位
    log_ret = np.log(df['close'] / df['close'].shift(1))
    vol = log_ret.rolling(60).std() * np.sqrt(252)
    df['hv_pctile'] = vol.rolling(250).apply(lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True)
    
    latest = df.iloc[-1]

    # ================== 4. 界面渲染 ==================
    
    st.title(f"📊 {SYMBOL_NAME}")

    # 第一排指标卡
    c1, c2, c3 = st.columns(3)
    c1.metric("最新价", f"¥{latest['close']:.2f}")
    c2.metric("HV60占比", f"{latest['hv_pctile']:.1%}")
    c3.metric("ATR波幅", f"{latest['atr']:.2f}")
    
    # 第二排均线卡
    c4, c5, c6 = st.columns(3)
    c4.metric("MA9(紫)", f"{latest['ma9']:.2f}")
    c5.metric("MA25(黄)", f"{latest['ma25']:.2f}")
    c6.metric("MA90(红)", f"{latest['ma90']:.2f}")

    # 趋势大图 (包含 ATR 副图)
    st.write("---")
    pdf = df.tail(120)
    # 修改为 3 行，给 ATR 留出空间
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.25, 0.25])
    
    # Row 1: K线与均线
    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='purple', width=1.5), name="MA9"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma25'], line=dict(color='#FFD700', width=1.5), name="MA25"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma90'], line=dict(color='red', width=1.5), name="MA90"), row=1, col=1)
    
    # Row 2: HV60分位
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['hv_pctile'], fill='tozeroy', line=dict(color='orange'), name="HV分位"), row=2, col=1)
    
    # Row 3: ATR 曲线 (重新加回)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['atr'], line=dict(color='blue', width=1.5), name="ATR"), row=3, col=1)
    
    fig.update_layout(
        height=750, 
        xaxis_rangeslider_visible=False, 
        template="plotly_white", 
        dragmode=False, 
        margin=dict(l=0,r=0,t=0,b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # 明细表
    st.subheader("📋 数据明细")
    table_df = df[['close', 'ma9', 'ma25', 'ma90', 'hv_pctile', 'atr']].tail(15).iloc[::-1]
    st.dataframe(table_df.style.format({'hv_pctile': '{:.1%}', 'close': '{:.2f}', 'atr': '{:.2f}'}), use_container_width=True)

else:
    st.error("数据加载失败。")