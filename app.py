import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json

# ================== 1. 网页配置 & 手机端样式 ==================
st.set_page_config(page_title="讯飞策略-平滑版", layout="wide")

st.markdown("""
    <style>
    .main .block-container {padding-top: 1rem; padding-left: 0.5rem; padding-right: 0.5rem;}
    [data-testid="stMetricValue"] {font-size: 1.1rem !important; font-weight: bold;}
    .modebar{display: none !important;}
    </style>
    """, unsafe_allow_html=True)

SYMBOL_NAME = "科大讯飞"
SYMBOL_CODE = "002230" 

# ================== 2. 强化版数据引擎 ==================

def get_sina_snapshot():
    url = f"https://hq.sinajs.cn/list=sz{SYMBOL_CODE}"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        raw = r.text.split('"')[1].split(',')
        if len(raw) > 30:
            return {'date': pd.to_datetime(raw[30] + " " + raw[31]), 'close': float(raw[3])}
    except: return None

def get_sina_min_line():
    """获取分时并剔除休市时段"""
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol=sz{SYMBOL_CODE}&scale=1&datalen=242"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text
        start, end = text.find('['), text.rfind(']') + 1
        if start != -1 and end != -1:
            df_min = pd.DataFrame(json.loads(text[start:end]))
            df_min['day'] = pd.to_datetime(df_min['day'])
            
            # 1. 只取今天
            today_mask = df_min['day'].dt.date == df_min['day'].dt.date.max()
            df_today = df_min[today_mask].sort_values('day').copy()
            
            # 2. 剔除中午休市时间 (11:30 - 13:00)
            # 我们只保留 09:30-11:30 和 13:00-15:00
            df_today = df_today[
                ((df_today['day'].dt.time >= datetime.strptime("09:30", "%H:%M").time()) & 
                 (df_today['day'].dt.time <= datetime.strptime("11:30", "%H:%M").time())) |
                ((df_today['day'].dt.time >= datetime.strptime("13:00", "%H:%M").time()) & 
                 (df_today['day'].dt.time <= datetime.strptime("15:00", "%H:%M").time()))
            ]
            return df_today
    except: pass
    return pd.DataFrame()

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

# ================== 3. 界面渲染 ==================

df_raw = get_historical_data()
if not df_raw.empty:
    df = df_raw.copy()
    snap = get_sina_snapshot()
    if snap and snap['date'].date() not in df.index:
        df.loc[snap['date'].date()] = [snap['close']]*4 
    
    df['ma9'] = df['close'].rolling(9).mean()
    df['ma25'] = df['close'].rolling(25).mean()
    df['ma90'] = df['close'].rolling(90).mean()
    
    log_ret = np.log(df['close'] / df['close'].shift(1))
    vol = log_ret.rolling(60).std() * np.sqrt(252)
    df['hv_pctile'] = vol.rolling(250).apply(lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True)
    latest = df.iloc[-1]

    # --- 顶部分值 ---
    c1, c2, c3 = st.columns(3)
    c1.metric("价格", f"¥{latest['close']:.2f}")
    c2.metric("HV60", f"{latest['hv_pctile']:.1%}")
    c3.metric("MA9", f"{latest['ma9']:.2f}")
    
    c4, c5 = st.columns(2)
    c4.metric("MA25", f"{latest['ma25']:.2f}")
    c5.metric("MA90", f"{latest['ma90']:.2f}")

    # --- 优化后的平滑分时图 ---
    st.write("---")
    df_min = get_sina_min_line()
    if not df_min.empty:
        fig_min = go.Figure()
        # 使用 line_shape='spline' 让线条变得平滑
        fig_min.add_trace(go.Scatter(
            x=df_min['day'], 
            y=df_min['close'], 
            mode='lines',
            line=dict(color='#00d2ff', width=3, shape='spline', smoothing=1.3), 
            name="分时实时"
        ))
        fig_min.update_layout(
            height=280, 
            template="plotly_white", 
            margin=dict(l=0,r=0,t=10,b=10),
            dragmode=False,
            xaxis=dict(fixedrange=True, tickformat="%H:%M", showgrid=False),
            yaxis=dict(fixedrange=True, side="right", tickformat=".2f")
        )
        st.plotly_chart(fig_min, use_container_width=True, config={'displayModeBar': False})

    # --- 趋势大图 ---
    pdf = df.tail(80)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='purple', width=1.5), name="MA9"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma25'], line=dict(color='#FFD700', width=1.5), name="MA25"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma90'], line=dict(color='red', width=1.5), name="MA90"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['hv_pctile'], fill='tozeroy', line=dict(color='orange'), name="HV"), row=2, col=1)
    
    fig.update_layout(height=500, xaxis_rangeslider_visible=False, template="plotly_white", dragmode=False, margin=dict(l=0,r=0,t=0,b=0))
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # --- 明细表 ---
    st.dataframe(df[['close', 'ma9', 'ma25', 'ma90', 'hv_pctile']].tail(10).iloc[::-1].style.format({'hv_pctile': '{:.1%}'}), use_container_width=True)