import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ================== 1. 核心配置 ==================
st.set_page_config(page_title="科大讯飞策略看板-云端版", layout="wide")

SYMBOL_NAME = "科大讯飞"
SYMBOL_CODE = "002230" 

# ================== 2. 数据引擎 ==================

def get_sina_snapshot():
    """获取最新成交价快照 - 云端兼容性极好"""
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
    """分时数据接口"""
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
    """获取历史数据 - 增加云端容错逻辑"""
    # 尝试登录 Baostock
    lg = bs.login()
    if lg.error_code != '0':
        st.error(f"Baostock 云端登录失败 (Error: {lg.error_msg})。建议在本地运行以获取完整 HV60 指标。")
        # 如果登录失败，尝试从新浪获取一个极简的单日数据防止后面报错
        snap = get_sina_snapshot()
        if snap:
            return pd.DataFrame([snap]).set_index('date')
        return pd.DataFrame()

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
        
        snap = get_sina_snapshot()
        if snap and snap['date'] not in df.index:
            new_row = pd.DataFrame([snap]).set_index('date')
            df = pd.concat([df, new_row])
    finally:
        bs.logout() # 无论成功失败都必须登出，否则占用云端Session
        
    return df.sort_index()

# ================== 3. 指标计算 ==================

def compute_indicators(df):
    if len(df) < 90: return df # 数据不足时不计算
    df = df.copy()
    df['ma9'] = df['close'].rolling(9).mean()
    df['ma25'] = df['close'].rolling(25).mean()
    df['ma90'] = df['close'].rolling(90).mean()
    pc = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'], (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(25).mean()
    log_return = np.log(df['close'] / df['close'].shift(1))
    vol = log_return.rolling(60).std() * np.sqrt(252)
    df['hv_pctile'] = vol.rolling(250).apply(lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True)
    return df

# ================== 4. 界面展示 ==================

st.title(f"📈 {SYMBOL_NAME} ({SYMBOL_CODE}) 策略决策引擎")

df_raw = get_combined_data()

if not df_raw.empty and len(df_raw) > 5:
    df = compute_indicators(df_raw)
    latest = df.iloc[-1]
    
    m1, m2, m3, m4 = st.columns(4)
    with m1: st.metric("当前价", f"¥{latest['close']:.2f}")
    with m2: 
        ma9_val = f"¥{latest['ma9']:.2f}" if 'ma9' in latest else "计算中"
        st.metric("MA9 (支撑)", ma9_val)
    with m3: 
        hv_val = f"{latest['hv_pctile']:.1%}" if 'hv_pctile' in latest and pd.notnull(latest['hv_pctile']) else "数据不足"
        st.metric("HV60 分位", hv_val)
    with m4: 
        atr_val = f"{latest['atr']:.2f}" if 'atr' in latest else "计算中"
        st.metric("ATR (25)", atr_val)

    # 分时模块 (云端最稳定的部分)
    st.subheader("🕙 实时走势")
    df_min = get_sina_min_line()
    if not df_min.empty:
        fig_min = go.Figure()
        fig_min.add_trace(go.Scatter(x=df_min['day'], y=df_min['close'], fill='tozeroy', line=dict(color='#00d2ff', width=1.5)))
        fig_min.update_layout(height=280, template="plotly_white", xaxis_tickformat="%H:%M")
        st.plotly_chart(fig_min, use_container_width=True)

    # 历史分析 (如果Baostock失效，这部分会显示空白)
    if 'ma9' in df.columns:
        st.subheader("📅 历史趋势分析")
        pdf = df.tail(180)
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.5, 0.25, 0.25])
        fig.add_trace(go.Candlestick(x=pdf.index, open=pdf.open, high=pdf.high, low=pdf.low, close=pdf.close, name="K线"), row=1, col=1)
        fig.add_trace(go.Scatter(x=pdf.index, y=pdf['ma9'], line=dict(color='purple', width=1), name="MA9"), row=1, col=1)
        fig.add_trace(go.Scatter(x=pdf.index, y=pdf['hv_pctile'], fill='tozeroy', name="HV60分位", line=dict(color='orange'), fillcolor='rgba(255, 165, 0, 0.2)'), row=2, col=1)
        fig.add_trace(go.Scatter(x=pdf.index, y=pdf['atr'], name="ATR", line=dict(color='blue', width=2)), row=3, col=1)
        fig.update_layout(height=800, xaxis_rangeslider_visible=False, template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("⚠️ 云端无法获取历史数据。这通常是由于 Baostock 接口在云端服务器受限导致。")
    st.info("💡 你依然可以查看下方实时价格（源自新浪接口）：")
    snap = get_sina_snapshot()
    if snap:
        st.write(f"**最新成交价：¥{snap['close']}** (时间：{snap['date']})")