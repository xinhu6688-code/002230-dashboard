import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# ================== 1. 页面配置与移动端样式注入 ==================
st.set_page_config(
    page_title="讯飞策略看板", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

# 注入 CSS 优化手机端显示
st.markdown("""
    <style>
    /* 移动端指标卡文字缩放 */
    [data-testid="stMetricValue"] {
        font-size: 1.8rem !important;
    }
    [data-testid="stMetricDelta"] {
        font-size: 0.9rem !important;
    }
    /* 减少移动端左右留白 */
    .block-container {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 2rem !important;
    }
    /* 隐藏顶部红线和菜单以增加可视面积 */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ================== 2. 策略参数 ==================
SYMBOL = "002230"
SYMBOL_BS = "sz.002230"
STOCK_NAME = "科大讯飞"
MA_FAST, MA_MID, MA_SLOW = 9, 25, 90
HV_PERIOD, PERCENTILE_WINDOW = 60, 250
REDUCE_THRESHOLD, ATR_PERIOD = 0.95, 25

# ================== 3. 数据处理逻辑 ==================
@st.cache_resource
def init_bs():
    bs.login()

def get_realtime():
    url = f"https://web.sqt.gtimg.cn/q=sz{SYMBOL}"
    try:
        r = requests.get(url, timeout=3)
        fields = r.text.split('~')
        if len(fields) > 34:
            return {
                "price": float(fields[3]), "open": float(fields[5]),
                "high": float(fields[33]), "low": float(fields[34]),
                "pct_chg": float(fields[32]), "volume": float(fields[36]),
                "update_time": datetime.now().strftime("%H:%M:%S")
            }
    except: return None

@st.cache_data(ttl=3600)
def get_historical(): 
    init_bs()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=950)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(SYMBOL_BS, "date,open,high,low,close,volume",
                                    start_date=start, end_date=end, frequency="d", adjustflag="2")
    data_list = []
    while (rs.error_code == '0') and rs.next(): data_list.append(rs.get_row_data())
    df = pd.DataFrame(data_list, columns=['date','open','high','low','close','volume'])
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df.astype(float).sort_index()

def compute_indicators(hist_df, realtime):
    today = pd.to_datetime(datetime.now().date())
    temp = pd.DataFrame({'open': realtime['open'], 'high': realtime['high'], 'low': realtime['low'], 
                         'close': realtime['price'], 'volume': realtime['volume']}, index=[today])
    df = pd.concat([hist_df[hist_df.index.date < today.date()], temp])
    df = df[~df.index.duplicated(keep='last')].ffill()

    # 核心指标
    df['ma9'] = df['close'].rolling(MA_FAST).mean()
    df['ma25'] = df['close'].rolling(MA_MID).mean()
    df['ma90'] = df['close'].rolling(MA_SLOW).mean()
    df['ma9_chg'] = df['ma9'] - df['ma9'].shift(1)
    
    returns = df['close'].pct_change()
    df['hv'] = returns.rolling(HV_PERIOD, min_periods=10).std() * np.sqrt(252)
    df['hv_pctile'] = df['hv'].rolling(PERCENTILE_WINDOW).apply(
        lambda x: (x[:-1] < x[-1]).mean() if not np.isnan(x[-1]) else np.nan, raw=True
    )
    
    tr = pd.concat([df['high']-df['low'], (df['high']-df['close'].shift(1)).abs(), (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD, min_periods=1).mean()
    return df

# ================== 4. 界面渲染 ==================
st.subheader(f"📊 {STOCK_NAME} ({SYMBOL}) 实时看板")

hist, real = get_historical(), get_realtime()

if hist is not None and real is not None:
    df = compute_indicators(hist, real)
    curr = df.iloc[-1]

    # 看板指标 (在手机端会自动堆叠)
    cols = st.columns(2) if st.session_state.get('mobile') else st.columns(4)
    # 使用两行显示以适配竖屏
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    c1.metric("最新", f"{real['price']:.2f}", f"{real['pct_chg']}%")
    c2.metric(f"MA{MA_FAST}", f"{curr['ma9']:.2f}", f"{curr['ma9_chg']:+.2f}")
    c3.metric("HV分位", f"{curr['hv_pctile']:.1%}")
    c4.metric("ATR", f"{curr['atr']:.2f}")

    # 图表绘制
    plot_df = df.dropna(subset=['hv_pctile']).tail(180)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.5, 0.25, 0.25])
    
    # 增加响应式配置
    fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df['open'], high=plot_df['high'], low=plot_df['low'], close=plot_df['close'], name="K线"), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ma9'], name="MA9", line=dict(color='orange', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ma25'], name="MA25", line=dict(color='cyan', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['ma90'], name="MA90", line=dict(color='red', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['hv_pctile'], name="HV分位", fill='tozeroy'), row=2, col=1)
    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['atr'], name="ATR", line=dict(color='green')), row=3, col=1)

    fig.update_layout(
        height=700, 
        margin=dict(l=10, r=10, t=20, b=20),
        xaxis_rangeslider_visible=False,
        autosize=True, # 关键：自适应宽度
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, width='stretch', config={'responsive': True})

    # 最近10天表
    st.markdown("##### 📋 10日关键参数")
    recent_10 = df.tail(10)[['close', 'ma9', 'ma25', 'hv_pctile', 'atr']].copy()
    recent_10.index = recent_10.index.strftime('%m-%d')
    st.dataframe(recent_10.style.format({'hv_pctile': '{:.1%}', 'close': '{:.2f}', 'ma9': '{:.2f}', 'ma25': '{:.2f}', 'atr': '{:.2f}'}), width='stretch')

    # 底部说明
    with st.expander("📖 策略逻辑"):
        st.caption("均线金叉 + HV极端值风控 + ATR动态波幅止损。HV分位 > 95% 建议减仓。")
else:
    st.error("数据连接失败")