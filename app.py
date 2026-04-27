import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json

# ================== 1. 核心配置 ==================
st.set_page_config(page_title="科大讯飞策略看板-完整实战版", layout="wide")

SYMBOL_NAME = "科大讯飞"
SYMBOL_CODE = "002230" 

# ================== 2. 数据引擎 ==================

def get_sina_snapshot():
    """获取最新成交价快照"""
    url = f"https://hq.sinajs.cn/list=sz{SYMBOL_CODE}"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        raw = r.text.split('"')[1].split(',')
        if len(raw) > 30:
            return {'date': pd.to_datetime(raw[30]), 'open': float(raw[1]), 'high': float(raw[4]), 'low': float(raw[5]), 'close': float(raw[3])}
    except: return None

def get_sina_min_line():
    """强化版分时数据抓取"""
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol=sz{SYMBOL_CODE}&scale=1&datalen=242"
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text
        # 剥离新浪接口多余的括号
        start = text.find('[')
        end = text.rfind(']') + 1
        if start != -1 and end != -1:
            data_list = json.loads(text[start:end])
            df_min = pd.DataFrame(data_list)
            if not df_min.empty:
                df_min['day'] = pd.to_datetime(df_min['day'])
                # 只取当天的分时数据
                current_day = df_min['day'].dt.date.max()
                return df_min[df_min['day'].dt.date == current_day].copy()
    except Exception as e:
        st.sidebar.error(f"分时接口异常: {e}")
    return pd.DataFrame()

@st.cache_data(ttl=300)
def get_combined_data():
    """获取历史数据"""
    bs.login()
    try:
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
        
        # 拼入今日盘中快照
        snap = get_sina_snapshot()
        if snap and snap['date'] not in df.index:
            new_row = pd.DataFrame([snap]).set_index('date')
            df = pd.concat([df, new_row])
    finally:
        bs.logout()
    return df.sort_index()

# ================== 3. 指标计算 ==================

def compute_indicators(df):
    df = df.copy()
    # 找回所有丢失的均线
    df['ma9'] = df['close'].rolling(9, min_periods=1).mean()
    df['ma25'] = df['close'].rolling(25, min_periods=1).mean()
    df['ma90'] = df['close'].rolling(90, min_periods=1).mean()
    
    # ATR 计算
    pc = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(25, min_periods=1).mean()
    
    # HV60 占比计算
    log_return = np.log(df['close'] / df['close'].shift(1))
    vol = log_return.rolling(60, min_periods=1).std() * np.sqrt(252)
    df['hv_pctile'] = vol.rolling(250, min_periods=1).apply(lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True)
    return df

# ================== 4. 界面展示 ==================

st.title(f"🚀 {SYMBOL_NAME} ({SYMBOL_CODE}) 决策看板")

df_raw = get_combined_data()

if not df_raw.empty:
    df = compute_indicators(df_raw)
    latest = df.iloc[-1]
    
    # 顶部数据卡片
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("最新价", f"¥{latest['close']:.2f}")
    m2.metric("MA9 (紫色)", f"¥{latest['ma9']:.2f}")
    m3.metric("HV60 分位", f"{latest['hv_pctile']:.1%}" if pd.notnull(latest['hv_pctile']) else "计算中")
    m4.metric("ATR 波动", f"{latest['atr']:.2f}")

    # --- 找回分时图 ---
    st.subheader("🕙 实时分时走势")
    df_min = get_sina_min_line()
    if not df_min.empty:
        fig_min = go.Figure()
        fig_min.add_trace(go.Scatter(x=df_min['day'], y=df_min['close'], fill='tozeroy', 
                                    line=dict(color='#00d2ff', width=2), name="分时"))
        fig_min.update_layout(height=300, template="plotly_white", margin=dict(l=10,r=10,t=10,b=10),
                            xaxis_tickformat="%H:%M")
        st.plotly_chart(fig_min, use_container_width=True)
    else:
        st.info("正在获取开盘实时分时数据...")

    # --- 找回历史 K 线与 MA25, MA90 ---
    st.subheader("📅 深度趋势分析")
    pdf = df.tail(120)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.25, 0.25])
    
    # 1. K线 + 均线簇
    fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='purple', width=1.5), name="MA9"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma25'], line=dict(color='#FFD700', width=1.5), name="MA25"), row=1, col=1)
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma90'], line=dict(color='red', width=1.5), name="MA90"), row=1, col=1)
    
    # 2. HV60 分位面积图
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['hv_pctile'], fill='tozeroy', name="HV60分位", 
                             line=dict(color='orange'), fillcolor='rgba(255, 165, 0, 0.15)'), row=2, col=1)
    
    # 3. ATR 
    fig.add_trace(go.Scatter(x=pdf.index, y=pdf['atr'], name="ATR", line=dict(color='blue', width=1.5)), row=3, col=1)
    
    fig.update_layout(height=800, xaxis_rangeslider_visible=False, template="plotly_white")
    st.plotly_chart(fig, use_container_width=True)

    # --- 找回底部的指标统计表 ---
    st.subheader("📋 指标明细 (最近15个交易日)")
    table_df = df[['close', 'ma9', 'ma25', 'ma90', 'hv_pctile', 'atr']].tail(15).iloc[::-1]
    st.dataframe(table_df.style.format({
        'close': '¥{:.2f}', 'ma9': '¥{:.2f}', 'ma25': '¥{:.2f}', 
        'ma90': '¥{:.2f}', 'hv_pctile': '{:.2%}', 'atr': '{:.2f}'
    }), use_container_width=True)

else:
    st.error("数据初始化失败，请检查 Baostock 登录状态。")