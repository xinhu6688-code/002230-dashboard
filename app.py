import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ================== 1. 核心配置 ==================
st.set_page_config(page_title="科大讯飞策略看板", layout="wide")

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
            return {
                'date': pd.to_datetime(raw[30]),
                'open': float(raw[1]), 
                'high': float(raw[4]),
                'low': float(raw[5]), 
                'close': float(raw[3])
            }
    except:
        return None

def get_sina_min_line():
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol=sz{SYMBOL_CODE}&scale=1&datalen=242"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text
        start, end = text.find('['), text.rfind(']') + 1
        if start != -1 and end != -1:
            pure_json = text[start:end]
            if not pure_json.endswith(']'): pure_json += ']'
            df_min = pd.read_json(pure_json)
            if not df_min.empty:
                df_min['day'] = pd.to_datetime(df_min['day'])
                last_day = df_min['day'].dt.date.max()
                return df_min[df_min['day'].dt.date == last_day].copy()
    except: pass
    return pd.DataFrame()

@st.cache_data(ttl=300)
def get_combined_data():
    bs.login()
    # 【修复2：数据量】拉取 730 天（2年）数据，确保 HV60 分位有足够参考系
    start_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(f"sz.{SYMBOL_CODE}", "date,open,high,low,close",
                                    start_date=start_date, end_date="", frequency="d", adjustflag="2")
    data = []
    while rs.next(): data.append(rs.get_row_data())
    
    df = pd.DataFrame(data, columns=['date','open','high','low','close'])
    df['date'] = pd.to_datetime(df['date'])
    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.set_index('date', inplace=True)
    
    snap = get_sina_snapshot()
    if snap and snap['date'] not in df.index:
        new_row = pd.DataFrame([snap]).set_index('date')
        df = pd.concat([df, new_row])
    
    bs.logout()
    return df.sort_index()

# ================== 3. 指标计算 ==================

def compute_indicators(df):
    df = df.copy()
    # 均线组
    df['ma9'] = df['close'].rolling(9).mean()
    df['ma25'] = df['close'].rolling(25).mean()
    df['ma90'] = df['close'].rolling(90).mean()
    
    # ATR
    pc = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(25).mean()
    
    # HV60 波动分位计算
    log_return = np.log(df['close'] / df['close'].shift(1))
    vol = log_return.rolling(60).std() * np.sqrt(252)
    # 计算当前波动率在过去 250 个交易日内的排名百分比
    df['hv_pctile'] = vol.rolling(250).apply(
        lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True
    )
    return df

# ================== 4. 渲染 ==================

st.title(f"🚀 {SYMBOL_NAME} ({SYMBOL_CODE}) 完整决策看板")

df_raw = get_combined_data()

if not df_raw.empty:
    df = compute_indicators(df_raw)
    latest = df.iloc[-1]
    
    # 指标卡
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("最新价", f"¥{latest['close']:.2f}")
    with c2: st.metric("MA9 (短期支撑)", f"¥{latest['ma9']:.2f}")
    with c3: 
        val = latest['hv_pctile']
        st.metric("HV60 波动分位", f"{val:.1%}" if not np.isnan(val) else "计算中...")
    with c4: st.metric("ATR 波动值", f"{latest['atr']:.2f}")

    # 分时图
    df_min = get_sina_min_line()
    if not df_min.empty:
        fig_min = go.Figure()
        fig_min.add_trace(go.Scatter(x=df_min['day'], y=df_min['close'], fill='tozeroy', name="分时", line=dict(color='#00d2ff')))
        fig_min.update_layout(height=250, title="今日分时", template="plotly_white")
        st.plotly_chart(fig_min, width='stretch')

    # 主力图表
    st.subheader("📅 深度趋势分析 (MA9/25/90 + HV60)")
    pdf = df.tail(200) # 展示最近 200 天数据
    
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, 
        vertical_spacing=0.05, 
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=("价格与均线系统", "HV60 波动率百分位 (反映波动剧烈程度)", "ATR (真实波幅)")
    )
    
    # 1. K线与均线 (【修复1：颜色】MA9 改为醒目的紫色)
    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='#9400D3', width=1.5), name="MA9 (紫)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma25'], line=dict(color='#FFD700', width=1.5), name="MA25 (金)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma90'], line=dict(color='#FF4500', width=1.5), name="MA90 (红)"), row=1, col=1)
    
    # 2. HV60 分位面积图
    fig.add_trace(go.Scatter(
        x=pdf.index, y=pdf['hv_pctile'], 
        fill='tozeroy', 
        name="HV60分位", 
        line=dict(color='#FF8C00', width=2),
        fillcolor='rgba(255, 140, 0, 0.2)'
    ), row=2, col=1)
    # 增加参考线
    fig.add_hline(y=0.8, line_dash="dash", line_color="red", row=2, col=1, annotation_text="高波")
    fig.add_hline(y=0.2, line_dash="dash", line_color="green", row=2, col=1, annotation_text="低波")

    # 3. ATR
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['atr'], name="ATR", line=dict(color='#1E90FF', width=2)), row=3, col=1)
    
    fig.update_layout(height=800, xaxis_rangeslider_visible=False, template="plotly_white")
    st.plotly_chart(fig, width='stretch')

    # 数据表格
    st.subheader("📋 策略原始数据 (最近15日)")
    st.dataframe(df[['close', 'ma9', 'ma25', 'hv_pctile', 'atr']].tail(15).iloc[::-1], width='stretch')

else:
    st.error("数据加载失败。")