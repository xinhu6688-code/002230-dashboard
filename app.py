import streamlit as st
import pandas as pd
import numpy as np
import baostock as bs
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import os

st.set_page_config(page_title="科大讯飞 实时策略看板", layout="wide", page_icon="📊")

# ================== 策略参数 ==================
SYMBOL = "002230"
SYMBOL_BS = "sz.002230"
STOCK_NAME = "科大讯飞"
MA_FAST, MA_SLOW, TREND_MA = 9, 25, 90
HV_PERIOD = 60
PERCENTILE_WINDOW = 250
REDUCE_THRESHOLD = 0.95
ATR_PERIOD = 25
ATR_STOP_MULT = 3
ATR_PROFIT_MULT = 5

DATA_FILE = "history.parquet"

# ================== 分时数据获取（稳定版本：取最近480条1分钟K线，过滤今日）==================
@st.cache_data(ttl=30, show_spinner=False)
def get_intraday_data():
    """获取今日分时数据（1分钟K线），返回时间、价格、成交量列表"""
    code = f"sz{SYMBOL}"
    url = f"http://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m1,,480"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == 0:
                m1_list = data['data'][code]['m1']
                if not m1_list:
                    return [], [], []
                today_str = datetime.now().strftime("%Y%m%d")
                records = []
                for item in m1_list:
                    time_str = item[0]
                    if not time_str.startswith(today_str):
                        continue
                    if len(time_str) < 12:
                        continue
                    hhmm = time_str[8:12]
                    if "0930" <= hhmm <= "1500":
                        records.append((f"{hhmm[:2]}:{hhmm[2:]}", float(item[4]), int(float(item[5]))))
                records.sort(key=lambda x: x[0])
                times = [r[0] for r in records]
                prices = [r[1] for r in records]
                volumes = [r[2] for r in records]
                return times, prices, volumes
            else:
                print(f"分时接口返回错误: {data.get('msg')}")
        else:
            print(f"分时接口HTTP错误: {resp.status_code}")
    except Exception as e:
        print(f"分时数据获取异常: {e}")
    return [], [], []

# ================== 实时行情多源主备 ==================
def get_realtime_tencent():
    try:
        url = f"https://web.sqt.gtimg.cn/q=sz{SYMBOL}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
        r = requests.get(url, headers=headers, timeout=5)
        r.encoding = 'gbk'
        raw = r.text.split('=')[1].strip('";\n')
        fields = raw.split('~')
        if len(fields) > 3:
            return {
                "price": float(fields[3]),
                "open": float(fields[5]),
                "close_yest": float(fields[4]),
                "pct_chg": float(fields[32]),
                "volume": float(fields[36]) / 10000,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
    except:
        return None

def get_realtime_sina():
    try:
        url = f"https://hq.sinajs.cn/list=sz{SYMBOL}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.text.split(',')
            if len(data) > 3:
                price = float(data[3])
                yest = float(data[2])
                return {
                    "price": price,
                    "open": float(data[1]),
                    "close_yest": yest,
                    "pct_chg": (price / yest - 1) * 100,
                    "volume": float(data[8]) / 10000,
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
    except:
        return None

def get_realtime_eastmoney():
    try:
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid=0.{SYMBOL}&fields=f43,f44,f45,f46,f47,f48,f49"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            d = r.json().get('data', {})
            if d:
                price = d.get('f43', 0) / 100
                yest = d.get('f44', 0) / 100
                return {
                    "price": price,
                    "open": d.get('f46', 0) / 100,
                    "close_yest": yest,
                    "pct_chg": (price / yest - 1) * 100 if yest else 0,
                    "volume": d.get('f47', 0) / 10000,
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
    except:
        return None

def get_realtime():
    sources = [get_realtime_tencent, get_realtime_sina, get_realtime_eastmoney]
    for src in sources:
        try:
            data = src()
            if data and data.get('price', 0) > 0:
                return data
        except:
            continue
    return None

# ================== 历史数据（baostock，本地缓存）==================
@st.cache_data(ttl=3600, show_spinner=False)
def get_historical():
    if os.path.exists(DATA_FILE):
        df = pd.read_parquet(DATA_FILE)
        last_date = df.index.max().date()
        if last_date < datetime.now().date():
            df = update_historical(df, last_date)
        return df
    else:
        df = fetch_from_baostock()
        if df is not None:
            df.to_parquet(DATA_FILE)
        return df

def fetch_from_baostock(days=600):
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days+100)).strftime("%Y-%m-%d")
    try:
        bs.login()
        rs = bs.query_history_k_data_plus(SYMBOL_BS,
            "date,open,high,low,close,volume",
            start_date=start, end_date=end,
            frequency="d", adjustflag="2")
        records = []
        while (rs.error_code == '0') and rs.next():
            records.append(rs.get_row_data())
        bs.logout()
        if not records:
            return None
        df = pd.DataFrame(records, columns=['date','open','high','low','close','volume'])
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df.astype(float).sort_index()
        return df.iloc[-days:]
    except Exception as e:
        st.error(f"baostock 历史数据获取失败: {e}")
        return None

def update_historical(df_old, last_date):
    start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    try:
        bs.login()
        rs = bs.query_history_k_data_plus(SYMBOL_BS,
            "date,open,high,low,close,volume",
            start_date=start, end_date=end,
            frequency="d", adjustflag="2")
        records = []
        while (rs.error_code == '0') and rs.next():
            records.append(rs.get_row_data())
        bs.logout()
        if records:
            df_new = pd.DataFrame(records, columns=['date','open','high','low','close','volume'])
            df_new['date'] = pd.to_datetime(df_new['date'])
            df_new.set_index('date', inplace=True)
            df_new = df_new.astype(float)
            df = pd.concat([df_old, df_new])
            df = df[~df.index.duplicated(keep='last')].sort_index()
            df.to_parquet(DATA_FILE)
            return df
        else:
            return df_old
    except Exception as e:
        st.warning(f"增量更新失败: {e}")
        return df_old

# ================== 指标计算 ==================
def compute_indicators(df_ohlc):
    if df_ohlc is None or len(df_ohlc) < PERCENTILE_WINDOW:
        return None
    close = df_ohlc['close']
    high = df_ohlc['high']
    low = df_ohlc['low']

    ma9 = close.rolling(MA_FAST).mean()
    ma25 = close.rolling(MA_SLOW).mean()
    ma90 = close.rolling(TREND_MA).mean()

    returns = close.pct_change()
    hv = returns.rolling(HV_PERIOD).std() * np.sqrt(252)

    # 手动计算分位（滞后一天）
    hv_pctile = pd.Series(index=hv.index, dtype=float)
    for i in range(PERCENTILE_WINDOW, len(hv)):
        window = hv.iloc[i-PERCENTILE_WINDOW:i]
        current = hv.iloc[i]
        pct = (window < current).sum() / len(window)
        hv_pctile.iloc[i] = pct
    hv_pctile.iloc[:PERCENTILE_WINDOW] = 0.5
    hv_pctile_lag = hv_pctile.shift(1)

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(ATR_PERIOD).mean()

    return pd.DataFrame({
        'ma9': ma9, 'ma25': ma25, 'ma90': ma90,
        'hv': hv, 'hv_pctile': hv_pctile_lag,
        'atr': atr
    }, index=df_ohlc.index)

# ================== 策略状态机 ==================
def strategy_status(current_price, indicators, entry_price=None, entry_atr=None, has_reduced=False):
    if current_price is None or indicators is None:
        return "数据不足", "gray", "-", None, None, False
    ma9 = indicators['ma9'].iloc[-1]
    ma25 = indicators['ma25'].iloc[-1]
    ma90 = indicators['ma90'].iloc[-1]
    hv_pct = indicators['hv_pctile'].iloc[-1]
    atr = indicators['atr'].iloc[-1]

    buy_signal = (current_price > ma9) and (ma9 > ma25) and (current_price > ma90)
    reduce_signal = (entry_price is not None) and (not has_reduced) and (hv_pct > REDUCE_THRESHOLD)
    sell_signal = False
    if entry_price is not None and entry_atr is not None:
        stop_price = entry_price - ATR_STOP_MULT * entry_atr
        profit_price = entry_price + ATR_PROFIT_MULT * entry_atr
        if current_price <= stop_price or current_price >= profit_price:
            sell_signal = True
    if (current_price < ma9 and ma9 < ma25):
        sell_signal = True

    if entry_price is None:
        if buy_signal:
            return "持仓", "green", f"买入 @{current_price:.2f}", current_price, atr, False
        else:
            return "空仓", "gray", "等待金叉", None, None, False
    else:
        if sell_signal:
            return "空仓", "gray", f"清仓 @{current_price:.2f}", None, None, False
        elif reduce_signal:
            return "已减仓", "orange", f"减仓50% (HV分位 {hv_pct:.1%})", entry_price, entry_atr, True
        else:
            if has_reduced:
                return "已减仓", "orange", "持有剩余仓位", entry_price, entry_atr, True
            else:
                return "持仓", "green", "持有", entry_price, entry_atr, False

# ================== 页面布局 ==================
st.title(f"📈 {STOCK_NAME} 实时策略看板")
st.caption("数据源: 多源实时 + baostock历史 | 手动刷新页面更新数据")

# 1. 分时图
times, prices, volumes = get_intraday_data()
if times and prices:
    fig_intra = make_subplots(specs=[[{"secondary_y": True}]])
    fig_intra.add_trace(go.Scatter(x=times, y=prices, mode='lines', name='价格', line=dict(color='blue', width=1.5)), secondary_y=False)
    fig_intra.add_trace(go.Bar(x=times, y=volumes, name='成交量', marker_color='lightgreen', opacity=0.5), secondary_y=True)
    fig_intra.update_layout(title="今日分时走势 & 成交量 (9:30-15:00)", height=450, xaxis_title="时间", hovermode='x unified')
    fig_intra.update_yaxes(title_text="价格 (元)", secondary_y=False)
    fig_intra.update_yaxes(title_text="成交量 (手)", secondary_y=True)
    st.plotly_chart(fig_intra, width='stretch')
else:
    st.info("分时数据暂不可用（非交易时段或数据加载中），请稍后刷新")

# 2. 实时行情卡片
realtime = get_realtime()
if realtime is None:
    st.error("实时数据获取失败，使用昨日收盘价作为参考")
    hist = get_historical()
    if hist is not None:
        realtime = {"price": hist['close'].iloc[-1], "pct_chg": 0, "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    else:
        st.stop()

# 历史数据
hist = get_historical()
if hist is None or hist.empty:
    st.error("历史数据获取失败，请检查网络")
    st.stop()

indicators = compute_indicators(hist)
if indicators is None:
    st.error("历史数据不足，无法计算指标")
    st.stop()

# 策略状态
if 'entry_price' not in st.session_state:
    st.session_state.entry_price = None
if 'entry_atr' not in st.session_state:
    st.session_state.entry_atr = None
if 'has_reduced' not in st.session_state:
    st.session_state.has_reduced = False

status, color, action, new_entry, new_atr, new_reduced = strategy_status(
    realtime['price'], indicators,
    st.session_state.entry_price, st.session_state.entry_atr,
    st.session_state.has_reduced
)
st.session_state.entry_price = new_entry
st.session_state.entry_atr = new_atr
st.session_state.has_reduced = new_reduced

# 指标卡片
col1, col2, col3, col4 = st.columns(4)
col1.metric("最新价", f"{realtime['price']:.2f} 元", delta=f"{realtime.get('pct_chg',0):.2f}%")
col2.metric("MA9", f"{indicators['ma9'].iloc[-1]:.2f}", delta=f"{(realtime['price']/indicators['ma9'].iloc[-1]-1)*100:.2f}%", delta_color="inverse")
col3.metric("MA25", f"{indicators['ma25'].iloc[-1]:.2f}", delta=f"{(realtime['price']/indicators['ma25'].iloc[-1]-1)*100:.2f}%", delta_color="inverse")
col4.metric("MA90", f"{indicators['ma90'].iloc[-1]:.2f}", delta=f"{(realtime['price']/indicators['ma90'].iloc[-1]-1)*100:.2f}%", delta_color="inverse")

col5, col6, col7 = st.columns(3)
col5.metric("HV分位", f"{indicators['hv_pctile'].iloc[-1]:.1%}")
col6.metric(f"ATR({ATR_PERIOD})", f"{indicators['atr'].iloc[-1]:.2f}")
col7.metric("策略状态", status, delta=action, delta_color="off")

st.info(f"**当前建议：{action}**")
st.caption(f"开仓条件：价格 > MA9 > MA25 且 价格 > MA90 | 减仓条件：HV分位 > {REDUCE_THRESHOLD*100:.0f}% | 清仓条件：死叉 或 价格跌破开仓价-{ATR_STOP_MULT}*ATR 或 涨超开仓价+{ATR_PROFIT_MULT}*ATR")

# 3. 技术指标复合图（4子图）
fig = make_subplots(
    rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
    row_heights=[0.5, 0.15, 0.15, 0.2],
    subplot_titles=("K线图 & 均线", "HV(60) 历史波动率 (年化)", "HV分位 (250日窗口)", f"ATR({ATR_PERIOD})")
)
fig.add_trace(go.Candlestick(x=hist.index, open=hist['open'], high=hist['high'],
                             low=hist['low'], close=hist['close'], name="K线"), row=1, col=1)
fig.add_trace(go.Scatter(x=indicators.index, y=indicators['ma9'], mode='lines',
                         name=f'MA{MA_FAST}', line=dict(color='orange')), row=1, col=1)
fig.add_trace(go.Scatter(x=indicators.index, y=indicators['ma25'], mode='lines',
                         name=f'MA{MA_SLOW}', line=dict(color='blue')), row=1, col=1)
fig.add_trace(go.Scatter(x=indicators.index, y=indicators['ma90'], mode='lines',
                         name=f'MA{TREND_MA}', line=dict(color='red')), row=1, col=1)
fig.add_trace(go.Scatter(x=[hist.index[-1]], y=[realtime['price']], mode='markers',
                         marker=dict(color='green', size=10), name='实时价'), row=1, col=1)

fig.add_trace(go.Scatter(x=indicators.index, y=indicators['hv'], mode='lines',
                         name='HV(60)', line=dict(color='purple')), row=2, col=1)
fig.update_yaxes(title_text="年化波动率", row=2, col=1, tickformat=".0%")

fig.add_trace(go.Scatter(x=indicators.index, y=indicators['hv_pctile'], mode='lines',
                         name='HV分位', line=dict(color='orange')), row=3, col=1)
fig.add_hline(y=REDUCE_THRESHOLD, line_dash="dash", line_color="red", row=3, col=1,
              annotation_text=f"减仓阈值 {REDUCE_THRESHOLD*100:.0f}%")
fig.update_yaxes(title_text="分位数", row=3, col=1, tickformat=".0%")

fig.add_trace(go.Scatter(x=indicators.index, y=indicators['atr'], mode='lines',
                         name=f'ATR({ATR_PERIOD})', line=dict(color='teal')), row=4, col=1)
fig.update_yaxes(title_text="ATR (元)", row=4, col=1)

fig.update_layout(title=f"{STOCK_NAME} 技术指标与策略信号", height=1200,
                  xaxis_title="日期", legend=dict(orientation="h", yanchor="bottom", y=1.02))
fig.update_xaxes(rangeslider_visible=False)
st.plotly_chart(fig, width='stretch')

st.caption(f"实时数据时间: {realtime.get('update_time', '')} | 历史数据截止: {hist.index[-1].strftime('%Y-%m-%d')} | 手动刷新页面")