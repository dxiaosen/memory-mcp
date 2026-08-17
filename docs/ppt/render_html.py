"""用 HTML + CSS + Playwright 渲染答辩 PPT 内容图为高清 PNG。

浏览器有真正的字体度量、word-break、flexbox，文字溢出问题从根上消失。
画布固定 1210×585px（对应 12.1×5.85 英寸，PPT 插入用）。
输出到 docs/ppt/imgs/，供 build_ppt.py 插入。
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "imgs"
OUT.mkdir(exist_ok=True)

W, H = 1210, 585

C = {
    "navy": "#005982", "blue": "#2A7AB0", "red": "#D60012", "wine": "#B23A3A", "green": "#2E8B57",
    "orange": "#C86100", "ink": "#262B33", "mid": "#666666", "light": "#E2E8EC",
    "vly": "#F5F8FA", "pblue": "#DDEEF6", "pred": "#F8E7E9", "pgreen": "#E3F1EA",
    "porange": "#FCF0DB", "white": "#FFFFFF", "gray": "#9BABB4",
    "deepnavy": "#11497a", "darknavy": "#0a3a66",
    "slate": "#4A6670", "teal": "#2C6E6B",
}

CSS = f"""
@font-face {{ font-family:'NotoSans'; src:local('Noto Sans CJK SC'),local('Noto Sans SC'),local('Microsoft YaHei'); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden;
  font-family:'NotoSans','Noto Sans CJK SC','Microsoft YaHei','SimHei',sans-serif;
  font-size:16px; color:{C['ink']}; background:{C['white']}; -webkit-font-smoothing:antialiased; }}
.page {{ width:{W}px; height:{H}px; position:relative; overflow:hidden; }}
.h2 {{ font-size:22px; font-weight:800; color:{C['navy']}; }}
.sub {{ font-size:13.5px; color:{C['mid']}; }}
.card {{ background:{C['vly']}; border:1px solid {C['light']}; border-radius:10px; overflow:hidden; }}
table {{ border-collapse:collapse; width:100%; }}
th,td {{ border:1px solid {C['light']}; padding:9px 6px; text-align:center; }}
"""


def _page(body: str, extra: str = "") -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{extra}</style></head><body><div class='page'>{body}</div></body></html>"


# ===== P02 痛点（横排三卡，留白干净）=====
def pains_full():
    # 统一 navy 配色，靠编号和痛点名区分；白底干净
    items = [
        ("忘", "记不住", "跨会话断档",
         ["上一轮确认过的偏好和结论，下一轮即失效",
          "项目背景需逐轮重新交代",
          "未决问题、推进进度等上下文存不住"],
         "上下文反复重建，算力浪费，响应变慢"),
        ("串", "串味", "身份边界失守",
         ["多人共用同一记忆，无归属区分",
          "A 的私有判断出现在 B 的召回结果中",
          "此类泄漏一旦发生无法收回"],
         "私有数据泄漏，合规与信任双重受损"),
        ("乱", "没法管", "只增不改不废",
         ["旧结论与新结论并存，无法判别有效性",
          "无法修订、作废，亦无法追溯出处",
          "记忆一旦写入即固化，越积越脏"],
         "记忆不可信，最终只能整体废弃重建"),
    ]
    cards = ""
    for g, h, slogan, pts, cost in items:
        pts_html = "".join(
            f"<div style='display:flex;gap:12px;align-items:flex-start;font-size:18.5px;line-height:1.6;color:{C['ink']};'>"
            f"<span style='color:{C['navy']};font-weight:800;flex-shrink:0;font-size:20px;'>{i+1}.</span>"
            f"<span>{p}</span></div>" for i, p in enumerate(pts))
        cards += f"""
        <div class='card' style='flex:1;position:relative;padding:0;display:flex;flex-direction:column;overflow:hidden;background:#fff;border:1px solid {C['light']};'>
          <div style='height:12px;background:{C['navy']};'></div>
          <div style='padding:28px 24px 0 24px;flex:1;display:flex;flex-direction:column;'>
            <div style='display:flex;align-items:center;gap:16px;'>
              <div style='width:66px;height:66px;border-radius:50%;background:{C['navy']};color:#fff;font-size:36px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;'>{g}</div>
              <div>
                <div style='font-size:27px;font-weight:800;color:{C['navy']};line-height:1.1;'>{h}</div>
                <div style='font-size:15px;color:{C['navy']};font-weight:700;margin-top:5px;'>{slogan}</div>
              </div>
            </div>
            <div style='margin-top:26px;display:flex;flex-direction:column;gap:16px;flex:1;'>{pts_html}</div>
          </div>
          <div style='margin:0 22px 22px 22px;padding:20px 22px;background:{C['vly']};border-left:5px solid {C['navy']};border-radius:6px;'>
            <div style='font-size:21px;color:{C['mid']};margin-bottom:9px;font-weight:800;'>后果</div>
            <div style='font-size:23px;font-weight:800;color:{C['navy']};line-height:1.4;'>{cost}</div>
          </div>
        </div>"""
    body = f"""
    <div style='padding:20px 30px 0 30px;display:flex;gap:20px;height:498px;'>{cards}</div>
    <div style='position:absolute;bottom:30px;left:0;right:0;text-align:center;font-size:20px;font-weight:700;color:{C['navy']};'>
      痛点不在存储，而在边界、可改、可追溯 —— 这是立项出发点
    </div>"""
    return _page(body)


# ===== P03 根因与对策：为什么独立成服务 =====
def why_service():
    """承接 P02 痛点 → 根因是记忆散落各自管 → 对策是独立成服务。左右对比拓扑。"""
    body = f"""
    <div style='padding:20px 30px 0 30px;'>
      <div class='h2'>痛点的根因：记忆散落在各 Agent 内，无统一治理</div>
      <div class='sub' style='margin-top:5px;'>忘、串、乱的根因不在存储能力，而在抽取、隔离、准入、生命周期无人统一负责——故应抽成独立服务</div>
    </div>
    <div style='position:absolute;top:96px;left:0;right:0;padding:0 30px;'>
      <svg viewBox='0 0 1154 440' width='1154' height='440'>
        <!-- 左：现状 -->
        <text x='280' y='26' text-anchor='middle' fill='{C['mid']}' font-size='17' font-weight='800'>现状：每个 Agent 自建记忆</text>
        <rect x='60' y='55' width='440' height='40' rx='8' fill='#fff' stroke='{C['mid']}' stroke-width='1.5'/>
        <text x='120' y='80' text-anchor='middle' fill='{C['mid']}' font-size='14' font-weight='700'>Agent A</text>
        <text x='280' y='80' text-anchor='middle' fill='{C['mid']}' font-size='14' font-weight='700'>Agent B</text>
        <text x='440' y='80' text-anchor='middle' fill='{C['mid']}' font-size='14' font-weight='700'>Agent C</text>
        <!-- 三个各自的记忆块 -->
        <rect x='65' y='130' width='130' height='130' rx='10' fill='{C['vly']}' stroke='{C['mid']}' stroke-width='1.2' stroke-dasharray='5,3'/>
        <text x='130' y='170' text-anchor='middle' fill='{C['mid']}' font-size='15' font-weight='800'>自带记忆</text>
        <text x='130' y='200' text-anchor='middle' fill='{C['mid']}' font-size='12'>抽取各自实现</text>
        <text x='130' y='220' text-anchor='middle' fill='{C['mid']}' font-size='12'>隔离各自实现</text>
        <text x='130' y='240' text-anchor='middle' fill='{C['mid']}' font-size='12'>规则各自定义</text>
        <rect x='215' y='130' width='130' height='130' rx='10' fill='{C['vly']}' stroke='{C['mid']}' stroke-width='1.2' stroke-dasharray='5,3'/>
        <text x='280' y='170' text-anchor='middle' fill='{C['mid']}' font-size='15' font-weight='800'>自带记忆</text>
        <text x='280' y='200' text-anchor='middle' fill='{C['mid']}' font-size='12'>抽取各自实现</text>
        <text x='280' y='220' text-anchor='middle' fill='{C['mid']}' font-size='12'>隔离各自实现</text>
        <text x='280' y='240' text-anchor='middle' fill='{C['mid']}' font-size='12'>规则各自定义</text>
        <rect x='365' y='130' width='130' height='130' rx='10' fill='{C['vly']}' stroke='{C['mid']}' stroke-width='1.2' stroke-dasharray='5,3'/>
        <text x='430' y='170' text-anchor='middle' fill='{C['mid']}' font-size='15' font-weight='800'>自带记忆</text>
        <text x='430' y='200' text-anchor='middle' fill='{C['mid']}' font-size='12'>抽取各自实现</text>
        <text x='430' y='220' text-anchor='middle' fill='{C['mid']}' font-size='12'>隔离各自实现</text>
        <text x='430' y='240' text-anchor='middle' fill='{C['mid']}' font-size='12'>规则各自定义</text>
        <line x1='130' y1='95' x2='130' y2='130' stroke='{C['mid']}' stroke-width='1.5' stroke-dasharray='4,3'/>
        <line x1='280' y1='95' x2='280' y2='130' stroke='{C['mid']}' stroke-width='1.5' stroke-dasharray='4,3'/>
        <line x1='430' y1='95' x2='430' y2='130' stroke='{C['mid']}' stroke-width='1.5' stroke-dasharray='4,3'/>
        <!-- 三个后果 -->
        <text x='280' y='295' text-anchor='middle' fill='{C['mid']}' font-size='13.5'>数据多份不一致 · 隔离规则散落 · 每接入一种 Agent 须重写一遍</text>
        <!-- 中间箭头 -->
        <path d='M540 195 L 620 195' fill='none' stroke='{C['navy']}' stroke-width='3' marker-end='url(#wa)'/>
        <text x='580' y='178' text-anchor='middle' fill='{C['navy']}' font-size='14' font-weight='800'>对策</text>
        <text x='580' y='220' text-anchor='middle' fill='{C['navy']}' font-size='11.5'>独立成服务</text>
        <!-- 右：对策后 -->
        <text x='870' y='26' text-anchor='middle' fill='{C['navy']}' font-size='17' font-weight='800'>对策：记忆是独立服务，统一治理</text>
        <rect x='660' y='55' width='440' height='40' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='720' y='80' text-anchor='middle' fill='{C['navy']}' font-size='14' font-weight='700'>Agent A</text>
        <text x='870' y='80' text-anchor='middle' fill='{C['navy']}' font-size='14' font-weight='700'>Agent B</text>
        <text x='1040' y='80' text-anchor='middle' fill='{C['navy']}' font-size='14' font-weight='700'>Agent C</text>
        <!-- 统一服务大块 -->
        <rect x='680' y='130' width='400' height='130' rx='10' fill='{C['navy']}'/>
        <text x='880' y='168' text-anchor='middle' fill='#fff' font-size='18' font-weight='800'>Memory MCP Server</text>
        <text x='880' y='196' text-anchor='middle' fill='{C['pblue']}' font-size='13.5'>抽取 · 准入 · 召回 · 身份隔离 · 生命周期</text>
        <text x='880' y='220' text-anchor='middle' fill='{C['pblue']}' font-size='13.5'>服务端统一治理，Agent 经 MCP 接入</text>
        <text x='880' y='244' text-anchor='middle' fill='{C['pblue']}' font-size='11.5'>（标准协议 · 换 Agent 记忆不丢）</text>
        <line x1='720' y1='95' x2='730' y2='130' stroke='{C['navy']}' stroke-width='1.8'/>
        <line x1='870' y1='95' x2='880' y2='130' stroke='{C['navy']}' stroke-width='1.8'/>
        <line x1='1040' y1='95' x2='1030' y2='130' stroke='{C['navy']}' stroke-width='1.8'/>
        <text x='870' y='295' text-anchor='middle' fill='{C['navy']}' font-size='13.5' font-weight='700'>一份治理逻辑 · 数据一致 · 全程可审计</text>
        <!-- 底部类比条 -->
        <rect x='40' y='330' width='1074' height='96' rx='10' fill='{C['pblue']}' stroke='{C['navy']}' stroke-width='1'/>
        <text x='76' y='362' fill='{C['navy']}' font-size='16' font-weight='800'>类比：每个 App 自建数据库  →  用统一的 DB 服务</text>
        <text x='76' y='390' fill='{C['ink']}' font-size='14' line-height='1.6'>App 不应自实现存储引擎、事务、权限——此属基础设施。同理，Agent 不应自实现记忆的抽取、准入、隔离、生命周期。</text>
        <text x='76' y='414' fill='{C['ink']}' font-size='14'>记忆的复杂度在于治理而非存储——这是独立成服务的依据，亦为本系统立项出发点。</text>
        <defs><marker id='wa' markerWidth='10' markerHeight='10' refX='8' refY='5' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='{C['navy']}'/></marker></defs>
      </svg>
    </div>"""
    return _page(body)


# ===== 全量架构图（参考 MCP Server 形态展开）=====
def background_full():
    """三时间线 + 完整架构：用户→三种宿主→Agent Client（展开4能力）→Server（展开组件）→后端"""
    tl = [("MCP 发布前", "Agent 各自实现", "每套 Agent 自带记忆，数据散落、版本不一，更换即丢失", C["mid"]),
          ("2024 末", "MCP 协议发布", "记忆有了标准接入面，但抽取 / 隔离 / 治理仍各自实现", C["blue"]),
          ("2026·本系统", "独立记忆服务", "抽取 / 准入 / 召回 / 身份隔离，统一由服务端负责", C["navy"])]
    tlh = "".join(f"""
        <div class='card' style='flex:1;border:1.5px solid {c};border-top:5px solid {c};padding:14px 18px;'>
          <div style='display:flex;gap:8px;align-items:baseline;'>
            <span style='font-size:13px;font-weight:800;color:{c};'>{t}</span>
            <span style='font-size:16px;font-weight:800;color:{C['navy']};'>{h}</span>
          </div>
          <div style='font-size:12px;color:{C['ink']};margin-top:6px;line-height:1.5;'>{b}</div>
        </div>""" for t, h, b, c in tl)
    body = f"""
    <div style='padding:14px 30px 0 30px;'><div style='display:flex;gap:16px;'>{tlh}</div></div>
    <div style='padding:8px 30px 0 30px;'><div class='h2' style='font-size:19px;'>系统架构：Agent 只管接入，记忆治理全在服务端</div></div>
    <div style='position:absolute;top:122px;left:0;right:0;padding:0 30px;'>
      <svg viewBox='0 0 1154 410' width='1154' height='410'>
        <!-- 用户 -->
        <rect x='0' y='177' width='84' height='52' rx='8' fill='{C['mid']}'/>
        <text x='42' y='207' text-anchor='middle' fill='#fff' font-size='15' font-weight='700'>用户</text>
        <!-- 用户 -> 三个宿主 -->
        <path d='M84 193 L 118 66' fill='none' stroke='{C['navy']}' stroke-width='1.6' marker-end='url(#an)'/>
        <path d='M84 203 L 118 203' fill='none' stroke='{C['navy']}' stroke-width='1.6' marker-end='url(#an)'/>
        <path d='M84 213 L 118 340' fill='none' stroke='{C['navy']}' stroke-width='1.6' marker-end='url(#an)'/>
        <!-- 三种宿主 -->
        <rect x='118' y='40' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='207' y='70' text-anchor='middle' fill='{C['navy']}' font-size='12' font-weight='700'>Claude Code</text>
        <rect x='118' y='177' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='207' y='207' text-anchor='middle' fill='{C['navy']}' font-size='12' font-weight='700'>Codex / 通用CLI</text>
        <rect x='118' y='314' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='207' y='344' text-anchor='middle' fill='{C['navy']}' font-size='12' font-weight='700'>自定义 Agent</text>
        <!-- Agent Client 展开4能力 -->
        <rect x='331' y='40' width='236' height='326' rx='10' fill='{C['pblue']}' stroke='{C['navy']}' stroke-width='1.8' stroke-dasharray='6,3'/>
        <text x='449' y='67' text-anchor='middle' fill='{C['navy']}' font-size='14' font-weight='800'>Agent Client（轻量桥接）</text>
        <text x='449' y='85' text-anchor='middle' fill='{C['mid']}' font-size='10'>不侵入 Agent 业务代码</text>
        <rect x='344' y='113' width='210' height='44' rx='6' fill='#fff' stroke='{C['navy']}' stroke-width='1'/>
        <text x='449' y='131' text-anchor='middle' fill='{C['navy']}' font-size='11' font-weight='700'>BeforeRun 召回注入</text>
        <text x='449' y='147' text-anchor='middle' fill='{C['mid']}' font-size='9'>最多5条 · token预算600 · 15s</text>
        <rect x='344' y='177' width='210' height='44' rx='6' fill='#fff' stroke='{C['navy']}' stroke-width='1'/>
        <text x='449' y='195' text-anchor='middle' fill='{C['navy']}' font-size='11' font-weight='700'>AfterRun 捕获入队</text>
        <text x='449' y='211' text-anchor='middle' fill='{C['mid']}' font-size='9'>5s超时 · 幂等 · fail-open</text>
        <rect x='344' y='241' width='210' height='44' rx='6' fill='#fff' stroke='{C['navy']}' stroke-width='1'/>
        <text x='449' y='259' text-anchor='middle' fill='{C['navy']}' font-size='11' font-weight='700'>多宿主适配</text>
        <text x='449' y='275' text-anchor='middle' fill='{C['mid']}' font-size='9'>归一化到通用生命周期事件</text>
        <rect x='344' y='305' width='210' height='44' rx='6' fill='#fff' stroke='{C['navy']}' stroke-width='1'/>
        <text x='449' y='323' text-anchor='middle' fill='{C['navy']}' font-size='11' font-weight='700'>幂等去重</text>
        <text x='449' y='339' text-anchor='middle' fill='{C['mid']}' font-size='9'>run_key 三元组 · 1000条LRU</text>
        <!-- 宿主 -> client -->
        <path d='M297 66 L 331 66' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <path d='M297 203 L 331 203' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <path d='M297 340 L 331 340' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <!-- client -> server -->
        <line x1='567' y1='203' x2='601' y2='203' stroke='{C['navy']}' stroke-width='2.2' marker-end='url(#an)'/>
        <!-- Server 展开 -->
        <rect x='601' y='40' width='340' height='326' rx='12' fill='{C['navy']}'/>
        <text x='771' y='67' text-anchor='middle' fill='#fff' font-size='15' font-weight='800'>Memory MCP Server</text>
        <text x='771' y='85' text-anchor='middle' fill='#fff' font-size='11'>MCP 标准协议 · 13 个工具</text>
        <rect x='616' y='96' width='310' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='771' y='113' text-anchor='middle' fill='#fff' font-size='12'>准入策略（auto_save / pending / discard / replacement）</text>
        <rect x='616' y='136' width='310' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='771' y='153' text-anchor='middle' fill='#fff' font-size='12'>召回排序（向量 + 词法 + 关系三路融合）</text>
        <rect x='616' y='176' width='151' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='692' y='193' text-anchor='middle' fill='#fff' font-size='11.5'>生命周期管理</text>
        <rect x='774' y='176' width='152' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='850' y='193' text-anchor='middle' fill='#fff' font-size='11.5'>团队记忆提取</text>
        <rect x='616' y='216' width='310' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='771' y='233' text-anchor='middle' fill='#fff' font-size='12'>候选抽取（LLM） + 自动关系建边</text>
        <rect x='616' y='258' width='151' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='692' y='275' text-anchor='middle' fill='#fff' font-size='11.5'>身份隔离</text>
        <rect x='774' y='258' width='152' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='850' y='275' text-anchor='middle' fill='#fff' font-size='11.5'>维护循环</text>
        <rect x='616' y='300' width='310' height='34' rx='6' fill='{C['deepnavy']}'/>
        <text x='771' y='322' text-anchor='middle' fill='#fff' font-size='11.5'>脱敏 · 审计日志 · 账号/团队隔离</text>
        <!-- server -> 三个后端 -->
        <path d='M941 66 L 975 66' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <path d='M941 203 L 975 203' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <path d='M941 340 L 975 340' fill='none' stroke='{C['navy']}' stroke-width='1.8' marker-end='url(#an)'/>
        <!-- 后端 -->
        <rect x='975' y='40' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='1065' y='62' text-anchor='middle' fill='{C['navy']}' font-size='13' font-weight='700'>PostgreSQL</text>
        <text x='1065' y='80' text-anchor='middle' fill='{C['mid']}' font-size='10'>唯一权威存储</text>
        <rect x='975' y='177' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='1065' y='199' text-anchor='middle' fill='{C['navy']}' font-size='13' font-weight='700'>LLM / Embedding</text>
        <text x='1065' y='217' text-anchor='middle' fill='{C['mid']}' font-size='10'>抽取 + 向量化</text>
        <rect x='975' y='314' width='179' height='52' rx='8' fill='#fff' stroke='{C['navy']}' stroke-width='1.5'/>
        <text x='1065' y='336' text-anchor='middle' fill='{C['navy']}' font-size='13' font-weight='700'>Worker / 队列</text>
        <text x='1065' y='354' text-anchor='middle' fill='{C['mid']}' font-size='10'>异步抽取</text>
        <defs>
          <marker id='an' markerWidth='9' markerHeight='9' refX='7' refY='4.5' orient='auto'><path d='M0,0 L9,4.5 L0,9 z' fill='{C['navy']}'/></marker>
        </defs>
      </svg>
    </div>
    <div style='position:absolute;bottom:12px;left:30px;right:30px;background:{C['pblue']};border:1px solid {C['navy']};border-radius:8px;padding:11px 16px;text-align:center;font-size:14px;font-weight:700;color:{C['navy']};line-height:1.5;'>
      核心设计：记忆归属由身份决定，与 Agent 实现解耦 —— Agent Client 抹平宿主差异，服务端统一治理，故换 Agent 记忆不丢、不串、不脏
    </div>"""
    return _page(body)


# ===== P6 竞品对比表（撑满画布，行高加大）=====
def competitor_table():
    """4 列竞品对比表，撑满画布。TencentDB 按核实事实填。"""
    headers = ["维度", "ChatGPT\nMemory", "Mem0", "TencentDB\nAgent Memory", "Memory MCP"]
    rows = [
        ("接入形态", "平台内置", "SDK 嵌入", "独立服务·MCP 式", "MCP 标准协议"),
        ("身份隔离", "无（单账号）", "客户端自管", "有（团队/ACL）", "服务端强制（Token 派生）"),
        ("记忆结构", "扁平键值", "扁平事实", "L0–L3 分层", "带立场的判断（4 类）"),
        ("判断演进", "直接覆盖", "覆盖", "资产版本号（无审计链）", "改判断不改历史（留版本+provenance）"),
        ("团队记忆", "无", "无", "有（手动共享）", "自动提取共识"),
        ("失效治理", "黑箱", "半自动", "状态+撤销（无到期）", "准入+生命周期+到期+脱敏"),
    ]
    hd = "".join(
        f"<th style='background:{bg};color:#fff;font-size:16px;white-space:pre-line;padding:16px 8px;'>{h}</th>"
        for h, bg in [("维度", C["navy"]), ("ChatGPT\nMemory", C["blue"]), ("Mem0", C["blue"]),
                      ("TencentDB\nAgent Memory", C["deepnavy"]), ("Memory MCP", C["navy"])])
    bd = ""
    for ri, row in enumerate(rows):
        cells = ""
        for ci, cell in enumerate(row):
            if ci == 0:
                face, color, bold = C["vly"], C["navy"], "700"
            elif ci == 4:
                face, color, bold = C["pblue"], C["navy"], "800"
            elif ci == 3:
                face, color, bold = "#EAF1F4", C["deepnavy"], "600"
            else:
                face, color, bold = ("#fff" if ri % 2 == 0 else C["vly"]), C["ink"], "400"
            cells += f"<td style='background:{face};color:{color};font-weight:{bold};font-size:15.5px;padding:16px 8px;'>{cell}</td>"
        bd += f"<tr>{cells}</tr>"
    body = f"""
    <div style='padding:22px 30px 0 30px;'>
      <table style='table-layout:fixed;width:100%;'>
        <colgroup><col style='width:124px'/><col/><col/><col/><col/></colgroup>
        <thead><tr>{hd}</tr></thead>
        <tbody>{bd}</tbody>
      </table>
    </div>
    <div style='position:absolute;bottom:18px;left:30px;right:30px;background:{C['pblue']};border-left:6px solid {C['navy']};border-radius:8px;padding:16px 22px;'>
      <span style='font-size:17px;font-weight:800;color:{C['navy']};'>差异化</span>
      <span style='font-size:16.5px;color:{C['ink']};margin-left:14px;'>即便最接近的 TencentDB，仍差在三轴：判断演进审计链 · 团队自动共识 · 失效治理——Memory MCP 均已覆盖</span>
    </div>"""
    return _page(body, "th,td{font-size:15.5px;} table th,table td{padding:16px 8px;}")


# ===== P7 三个差异化（对照 TencentDB，gap 陈述，分点撑满等高）=====
def three_diffs():
    diffs = [
        ("01", "判断演进留审计链",
         "改判断不改历史，演进过程可追溯",
         ["判断被新结论取代时触发 replacement",
              "旧判断保留为版本链，记录推翻时间与依据",
              "投研结论会变，但演进过程与出处须留存"],
         "仅资产版本号字段，无法记录判断间因果关系",
         "版本链 + provenance + 审计，判断间因果可追溯"),
        ("02", "团队共识自动提取",
         "主动发现，非手动共享",
         ["周期性聚类多名成员的相似判断",
              "生成「待确认候选」，任一成员确认即成共识",
              "全员可见，无需成员主动发起共享"],
         "团队记忆依赖成员主动共享、人工审核",
         "自动聚类 → 主动提议候选 → 一人确认即共识"),
        ("03", "失效治理",
         "判断会到期、可作废、可追溯",
         ["判断带失效条件与有效期，到期自动 expired",
              "可主动 revoke 作废，槽位释放后可重建",
              "准入四类：auto_save / pending / discard / replacement"],
         "有状态与撤销共享，但无到期 TTL",
         "准入 + 生命周期 + 到期 + 脱敏，只存该存、只留该留"),
    ]
    cards = ""
    for n, h, sub, pts, gap_t, gap_m in diffs:
        pts_html = "".join(
              f"<div style='display:flex;gap:11px;align-items:flex-start;font-size:17px;line-height:1.55;color:{C['ink']};'>"
              f"<span style='color:{C['navy']};font-weight:800;flex-shrink:0;font-size:19px;'>{i+1}.</span>"
              f"<span>{p}</span></div>" for i, p in enumerate(pts))
        cards += f"""
        <div class='card' style='flex:1;position:relative;padding:0;display:flex;flex-direction:column;overflow:hidden;background:#fff;border:1px solid {C['light']};'>
          <div style='height:10px;background:{C['navy']};'></div>
          <div style='padding:22px 22px 0 22px;flex:1;display:flex;flex-direction:column;'>
            <div style='display:flex;align-items:center;gap:13px;'>
              <div style='font-size:40px;font-weight:800;color:{C['navy']};line-height:1;'>{n}</div>
              <div>
                <div style='font-size:21px;font-weight:800;color:{C['navy']};line-height:1.15;'>{h}</div>
                <div style='font-size:15px;color:{C['mid']};margin-top:4px;font-weight:700;line-height:1.55;min-height:46px;'>{sub}</div>
              </div>
            </div>
            <div style='margin-top:20px;display:flex;flex-direction:column;gap:15px;flex:1;'>{pts_html}</div>
          </div>
          <div style='margin:0 20px 20px 20px;background:{C['vly']};border-left:5px solid {C['navy']};border-radius:8px;padding:16px 18px;'>
            <div style='font-size:14.5px;line-height:1.55;color:{C['mid']};'>
              <span style='font-weight:800;color:{C['deepnavy']};'>TencentDB 缺：</span>{gap_t}
            </div>
            <div style='font-size:14.5px;line-height:1.55;color:{C['navy']};margin-top:7px;'>
              <span style='font-weight:800;'>Memory MCP 补：</span>{gap_m}
            </div>
          </div>
        </div>"""
    return _page(f"<div style='display:flex;gap:20px;padding:20px 28px;height:545px;'>{cards}</div>")


# ===== P9 核心闭环（去掉准入状态，丰富读写链路每个环节）=====
def core_loop():
    """上下两条链路，白底 navy 框 + 分区色带。字号放大，拉开间距，无底部条。
    业务逻辑经代码核实：抽取=结构化(非聚类)；召回=词法+向量+近期三路+关系感知补漏；
    准入=三级决策+replacement(auto_save 子动作)。"""
    # 链路色带：写入淡蓝、读取更浅
    band_w = "#EEF4F9"; band_r = "#F4F6F8"
    # 链路强调色：写入 navy、读取 blue
    wc, rc = C["navy"], C["blue"]

    def chain(items, y0, mk):
        """白底框 + 左侧色条 + 序号圆角块 + 标题 + 3 行说明。撑满半幅。"""
        n = len(items)
        gap = 20
        node_w = (1098 - (n - 1) * gap) / n
        node_h = 150
        x0 = 28
        parts = []
        for i, (num, label, lines, col) in enumerate(items):
            x = x0 + i * (node_w + gap)
            # 框：白底 + navy 边框
            parts.append(f"<rect x='{x:.0f}' y='{y0}' width='{node_w:.0f}' height='{node_h}' rx='9' fill='#fff' stroke='{C['light']}' stroke-width='1.5'/>")
            # 左侧色条
            parts.append(f"<rect x='{x:.0f}' y='{y0}' width='6' height='{node_h}' rx='3' fill='{col}'/>")
            cx = x + node_w / 2
            lx = x + 22   # 文字左对齐起点（避开色条）
            # 序号 + 标题同行
            parts.append(f"<text x='{x+18:.0f}' y='{y0+30}' fill='{col}' font-size='19' font-weight='800'>{num}</text>")
            parts.append(f"<text x='{x+46:.0f}' y='{y0+30}' fill='{C['navy']}' font-size='14.5' font-weight='800'>{label}</text>")
            # 分隔线
            parts.append(f"<line x1='{x+16:.0f}' y1='{y0+44}' x2='{x+node_w-16:.0f}' y2='{y0+44}' stroke='{C['light']}' stroke-width='1'/>")
            # 3 行业务逻辑
            for j, ln in enumerate(lines):
                parts.append(f"<text x='{x+18:.0f}' y='{y0+66+j*24}' fill='{C['ink']}' font-size='12.5'>{ln}</text>")
            # 箭头
            if i < n - 1:
                ax1 = x + node_w; ax2 = x + node_w + gap; ay = y0 + node_h / 2
                parts.append(f"<line x1='{ax1:.0f}' y1='{ay:.0f}' x2='{ax2-6:.0f}' y2='{ay:.0f}' stroke='{C['navy']}' stroke-width='2' marker-end='url(#{mk})'/>")
        return "".join(parts)

    write = [
        ("①", "Stop Hook 入队", ["Agent 输出完成即触发", "强制每轮提交，毫秒级返回", "身份/幂等字段服务端组装"], wc),
        ("②", "队列异步抽取", ["Worker 周期捞取 PENDING", "LLM 结构化抽取候选", "逐轮逐条，非跨轮聚类"], wc),
        ("③", "准入三级决策", ["auto_save 自动落库", "pending 待人确认一次", "discard 丢弃闲聊噪声"], wc),
        ("④", "replacement", ["auto_save 子动作", "命中显式替换意图时", "supersede 旧版，留版本链"], wc),
        ("⑤", "事务化落库", ["advisory lock 幂等提交", "memories/relations/reviews", "PostgreSQL 唯一权威"], wc),
    ]
    read = [
        ("①", "BeforeRun Hook", ["新一轮提问即触发", "召回由 Hook 自动完成", "模型无需自找记忆"], rc),
        ("②", "身份过滤前置", ["owner 从 Token 派生", "SQL WHERE 内先行过滤", "工具参数拒收 owner"], rc),
        ("③", "三路召回融合", ["词法 40%·向量 30%·近期 30%", "单 SQL 内 CTE 三路检索", "候选上限 500"], rc),
        ("④", "关系感知加权", ["补漏候选关系端点", "关系邻居 +0.12 加权", "阈值 0.18 过滤"], rc),
        ("⑤", "排序注入", ["Profile 优先级排序", "token 预算 600 内截断", "注入 additional_context"], rc),
    ]

    body = f"""
    <div style='padding:14px 28px 0 28px;'>
      <div class='h2'>核心闭环：写入异步治理，读取同步注入</div>
      <div class='sub' style='margin-top:3px;'>读写解耦——用户不等抽取；召回前服务端 SQL 内身份过滤防越权</div>
    </div>
    <div style='position:absolute;top:62px;left:0;right:0;'>
      <svg viewBox='0 0 1154 480' width='1154' height='480'>
        <!-- 写入色带 -->
        <rect x='14' y='10' width='1126' height='205' rx='12' fill='{band_w}'/>
        <text x='34' y='38' fill='{wc}' font-size='17' font-weight='800'>写入链路：异步治理，用户不手动存</text>
        <text x='34' y='57' fill='{C['mid']}' font-size='12'>模型不需自判存不存——Hook 强制每轮入队，准入由服务端统一决策</text>
        {chain(write, 70, 'wa')}
        <!-- 读取色带 -->
        <rect x='14' y='250' width='1126' height='205' rx='12' fill='{band_r}'/>
        <text x='34' y='278' fill='{rc}' font-size='17' font-weight='800'>读取链路：每轮自动召回，不让模型自己找</text>
        <text x='34' y='297' fill='{C['mid']}' font-size='12'>词法+向量+近期三路召回，关系感知加权，按 Profile 优先级在 token 预算内注入</text>
        {chain(read, 310, 'ra')}
        <defs>
          <marker id='wa' markerWidth='9' markerHeight='9' refX='7' refY='4.5' orient='auto'><path d='M0,0 L9,4.5 L0,9 z' fill='{C['navy']}'/></marker>
          <marker id='ra' markerWidth='9' markerHeight='9' refX='7' refY='4.5' orient='auto'><path d='M0,0 L9,4.5 L0,9 z' fill='{C['navy']}'/></marker>
        </defs>
      </svg>
    </div>"""
    return _page(body)


# ===== P10 记忆数据模型（详细解释治理字段）=====
def memory_model():
    """记忆数据模型 = 五张表结构图。顶部横向主轴(items→1:N→revisions 虚线框=两层结构)，
    下方三列附属表(evidence/reviews/relations)，表框自然高度+说明文字填空白。经 schema.sql 核实。"""
    def tbl(name, rows, accent, descs=None):
        """表框，自然高度。字段行：字段名(左)+解释(中,可选)+类型/PK/FK标签(右)。"""
        fr = ""
        for fname, ftype, ftag in rows:
            if ftag == "PK":
                right = f"<span style='color:{C['orange']};font-size:8px;font-weight:800;'>PK</span>"
            elif "FK" in ftag:
                right = (f"<span style='color:{C['gray']};font-size:8px;'>{ftype}</span>"
                         f"<span style='color:{C['mid']};font-size:8px;font-weight:700;margin-left:6px;white-space:nowrap;'>{ftag}</span>")
            else:
                right = f"<span style='color:{C['gray']};font-size:8px;'>{ftype}</span>"
            desc = descs.get(fname) if descs else None
            if desc:
                fr += (f"<div style='display:flex;justify-content:space-between;align-items:center;gap:6px;"
                       f"padding:3px 10px;border-bottom:1px solid {C['vly']};font-family:monospace;'>"
                       f"<span style='font-size:11.5px;color:{C['ink']};flex:0 0 auto;'>{fname}</span>"
                       f"<span style='font-size:8.5px;color:{C['gray']};line-height:1.25;flex:1;text-align:left;'>{desc}</span>"
                       f"<span style='flex:0 0 auto;'>{right}</span></div>")
            else:
                fr += (f"<div style='display:flex;justify-content:space-between;align-items:center;"
                       f"padding:3px 10px;border-bottom:1px solid {C['vly']};font-family:monospace;'>"
                       f"<span style='font-size:11.5px;color:{C['ink']};'>{fname}</span>"
                       f"<span>{right}</span></div>")
        return f"""
        <div style='border:1px solid {C['light']};border-radius:8px;overflow:hidden;background:#fff;flex:0 0 auto;'>
          <div style='background:{accent};color:#fff;padding:5px 12px;font-size:12.5px;font-weight:800;font-family:monospace;'>{name}</div>
          {fr}
        </div>"""

    def col(table_html, desc):
        """下方附属表列：表(顶) + 说明文字(flex:1 填底)。"""
        return f"""
        <div style='flex:1;display:flex;flex-direction:column;'>
          {table_html}
          <div style='flex:1;margin-top:8px;font-size:10.5px;line-height:1.55;color:{C['mid']};'>{desc}</div>
        </div>"""

    # 五张表（经 schema.sql 核实字段名/类型/关系；上区两表带字段解释）
    items_descs = {
        "memory_id": "记忆唯一 ID，跨版本不变——身份层",
        "owner_id": "归属身份（tenant:subject），隔离边界",
        "profile_id": "场景 Profile，决定 memory_type 取值域",
        "subject": "记忆主语，一句话概括（如某公司某判断）",
        "memory_type": "记忆类型，投研 8 类：thesis / risk / …",
        "lifecycle_status": "生命周期：active/superseded/expired/revoked",
    }
    rev_descs = {
        "revision_id": "版本快照 ID，每次修订生成新行",
        "memory_id": "FK→items，此版本属于哪条记忆",
        "revision_number": "版本号，递增；旧版不删只 superseded",
        "content": "记忆正文，本次版本的实际内容",
        "assertion_kind": "断言来源：user_view/外部 fact/系统推断",
        "lifecycle_status": "本版本状态（与 items 独立，支持旧版归档）",
    }
    items_tbl = tbl("memory_items", [
        ("memory_id", "UUID", "PK"), ("owner_id", "TEXT", ""), ("profile_id", "TEXT", ""),
        ("subject", "TEXT", ""), ("memory_type", "TEXT", ""), ("lifecycle_status", "TEXT", ""),
    ], C["navy"], items_descs)
    rev_tbl = tbl("memory_revisions", [
        ("revision_id", "UUID", "PK"), ("memory_id", "UUID", "FK→items"),
        ("revision_number", "INT", ""), ("content", "TEXT", ""),
        ("assertion_kind", "TEXT", ""), ("lifecycle_status", "TEXT", ""),
    ], C["deepnavy"], rev_descs)
    ev_tbl = tbl("memory_evidence", [
        ("evidence_id", "UUID", "PK"), ("revision_id", "UUID", "FK→revisions"),
        ("memory_id", "UUID", "FK→items"), ("source_turn_id", "TEXT", ""),
        ("source_expression", "TEXT", ""), ("source_tool_name", "TEXT", ""),
    ], C["navy"])
    rev2_tbl = tbl("memory_reviews", [
        ("review_id", "UUID", "PK"), ("candidate_id", "UUID", ""),
        ("subject", "TEXT", ""), ("content", "TEXT", ""),
        ("status", "TEXT", ""), ("resolved_memory_id", "UUID", "FK→items"),
    ], C["mid"])
    rel_tbl = tbl("memory_relations", [
        ("relation_id", "UUID", "PK"), ("source_memory_id", "UUID", "FK→items"),
        ("target_memory_id", "UUID", "FK→items"), ("relation_type", "TEXT", ""),
        ("status", "TEXT", ""),
    ], C["navy"])

    # 横向 1:N 箭头：items → revisions（两层结构主轴）
    harrow = f"""
    <div style='display:flex;flex-direction:column;align-items:center;justify-content:center;flex:0 0 auto;align-self:center;'>
      <div style='font-size:11px;font-weight:800;color:{C['navy']};'>1 : N</div>
      <div style='font-size:22px;color:{C['navy']};line-height:0.6;'>→</div>
    </div>"""
    layer_tag = f"""
    <div style='position:absolute;top:-9px;left:24px;background:#fff;padding:0 6px;font-size:10px;font-weight:800;color:{C['navy']};letter-spacing:0.5px;'>两层结构 · 主轴</div>"""
    l1 = f"<div style='font-size:9.5px;color:{C['mid']};font-weight:700;text-align:center;margin-bottom:3px;'>① 身份层</div>"
    l2 = f"<div style='font-size:9.5px;color:{C['mid']};font-weight:700;text-align:center;margin-bottom:3px;'>② 版本层</div>"

    # 附属表说明文字（核实语义）
    ev_desc = "每条证据绑定到具体 revision，记录哪一轮、哪条表达、哪个工具产出——可溯源到原始对话，拒绝无来源的记忆。"
    rv_desc = "候选进入 Pending 等人确认；确认后 resolved_memory_id 落到 items——可疑但不直接写库，确认才提升为正式记忆。"
    rl_desc = "source 与 target 都是 items 行，关系类型如 supports / challenges / supersedes——记忆间的论证结构靠此表自引用表达。"

    body = f"""
    <div style='padding:8px 28px 0 28px;'>
      <div class='h2' style='font-size:20px;'>记忆数据模型：五张表，两层结构</div>
      <div class='sub' style='margin-top:2px;font-size:12px;'>memory_items（稳定身份）1:N memory_revisions（版本快照）——旧版不删只 superseded；evidence / reviews / relations 通过 FK 引用主轴</div>
    </div>
    <!-- 上区：两层结构主轴（虚线框只包 items→revisions） -->
    <div style='position:relative;border:1.5px dashed {C['navy']};border-radius:10px;padding:14px 20px 12px;margin:14px 28px 0 28px;display:flex;align-items:flex-start;gap:18px;background:rgba(0,89,130,0.035);'>
      {layer_tag}
      <div style='flex:1;'>{l1}{items_tbl}</div>
      {harrow}
      <div style='flex:1;'>{l2}{rev_tbl}</div>
    </div>
    <!-- 下区：三张附属表横排（均在两层结构之外，FK 引用主轴） -->
    <div style='display:flex;gap:16px;margin:14px 28px 0 28px;align-items:stretch;'>
      <div style='flex:1;display:flex;flex-direction:column;'>{ev_tbl}<div style='flex:1;margin-top:6px;font-size:10.5px;line-height:1.55;color:{C['mid']};'>{ev_desc}</div></div>
      <div style='flex:1;display:flex;flex-direction:column;'>{rev2_tbl}<div style='flex:1;margin-top:6px;font-size:10.5px;line-height:1.55;color:{C['mid']};'>{rv_desc}</div></div>
      <div style='flex:1;display:flex;flex-direction:column;'>{rel_tbl}<div style='flex:1;margin-top:6px;font-size:10.5px;line-height:1.55;color:{C['mid']};'>{rl_desc}</div></div>
    </div>"""
    return _page(body)


# ===== P11 准入（候选处理流水线：四道防线 + reason_code 证据）=====
def admission_full():
    """候选从对话到入库的完整防线，对照 candidate_processing.process() 四段：
    ①来源校验 ②准入判定(6道规则) ③生命周期去重 ④分类写入。
    每段配真实 reason_code（代码核实），不只列规则。replacement/状态转换细节归 P13。"""
    # 四道防线：序号/名/职责/分点(专业中文)/色。沉稳配色：石板灰蓝→主蓝→深蓝→深青绿
    stages = [
        ("①", "来源校验", "防模型编造 · 防误抽",
         ["原文逐字核对：候选出处必须能在本轮对话原文里逐字找到，编造即丢单条",
          "操作指令剔除：「别用某工具、别联网」是临时指令，不是长期研究偏好",
          "敏感信息拦截：候选含密钥等敏感词直接拦截，不进库",
          "断言来源纠正：模型把助手推断误标成用户原话的，按真实来源纠正",
          "单条容错：一条候选出错只丢弃它，不影响同轮其它有效候选"],
         C["slate"]),
        ("②", "准入判定", "保守策略 · 六道顺序",
         ["临时内容丢弃：今日行情、盘中异动这类短期内容不进库",
          "存疑内容待审：模型拿不准的、用户明说「也许」的，先存疑等人确认",
          "系统推断待审：模型归纳的结论、含糊表达、低置信的，不替用户拍板",
          "非用户源降级：即便全过，助手或工具产出的仍降为待审",
          "全过方入库：只有用户明确、持久、高置信的判断才自动入库"],
         C["navy"]),
        ("③", "生命周期去重", "防重复 · 防碎片化",
         ["近似重复并入：与已有记忆语义相近的，并入旧记忆当证据，不新建条目",
          "显式替换取代：用户说「改成/调整/不再」时，新版本取代旧版、旧版标记归档",
          "替换碎片丢弃：一次修正产生的拆解碎片丢弃，防多条并存",
          "歧义降级待审：无法确定该更新哪条时，交人指认",
          "回声丢弃：助手复述已有记忆或跨类型复述的，视为回声丢弃"],
         C["darknavy"]),
        ("④", "分类写入", "落库分流 · 可审计",
         ["自动入库：写入主体表与版本表，进入有效状态参与召回",
          "待确认：写入待确认表，等人确认后才转为正式记忆",
          "不入库：只留处理记录备审计，不污染判断池"],
         C["teal"]),
    ]
    cols = ""
    for i, (num, name, role, points, c) in enumerate(stages):
        last = i == len(stages) - 1
        pts = ""
        for p in points:
            pts += (f"<div style='display:flex;gap:8px;align-items:flex-start;line-height:1.65;margin-bottom:13px;'>"
                    f"<span style='color:{c};font-weight:800;font-size:16px;flex:0 0 auto;line-height:1.4;'>●</span>"
                    f"<span style='font-size:13.5px;color:{C['ink']};'>{p}</span></div>")
        cols += f"""
        <div style='flex:1;display:flex;flex-direction:column;min-width:0;'>
          <div style='background:{c};color:#fff;border-radius:8px 8px 0 0;padding:13px 12px;text-align:center;flex:0 0 auto;'>
            <div style='font-size:21px;font-weight:800;'>{num} {name}</div>
            <div style='font-size:12px;opacity:0.92;margin-top:4px;'>{role}</div>
          </div>
          <div style='background:#fff;border:1px solid {c};border-top:none;border-radius:0 0 8px 8px;padding:16px 15px 18px;flex:1;display:flex;flex-direction:column;justify-content:flex-start;'>
            {pts}
          </div>
        </div>
        <div style='display:flex;align-items:center;color:{C['mid']};font-size:28px;padding:0 3px;flex:0 0 auto;'>{"→" if not last else ""}</div>"""
    body = f"""
    <div style='padding:14px 28px 0 28px;'>
      <div class='h2'>准入：候选到入库的四道防线——不只判规则，更防编造、防重复、防碎片化</div>
      <div class='sub' style='margin-top:4px;'>一条候选要过来源校验、六道保守判定、生命周期去重三关才落库；任一关拦截都留处理记录可审计，单条失败不拖垮整轮</div>
    </div>
    <div style='padding:18px 28px 0 28px;display:flex;align-items:stretch;gap:0;height:485px;'>{cols}</div>"""
    return _page(body)


# ===== P12 召回（HTML flexbox 流水线，统一卡片模板）=====
def recall_three_path():
    """三路混合召回 + 召回后加权打分。严格对照 recall.py _three_way_query + recall_service._score_record。
    三路 = 词法(40%)+向量(30%)+近期(30%)；关系是召回后加权(_RELATION_BOOST=0.12)，非独立路。
    打分常量：subject +0.20 / 向量 +0.15 / profile提示 +0.16 / 关系 +0.12 / 时效衰减0.15(半衰期90天)。
    阈值 _RELEVANCE_THRESHOLD=0.18。优先级：偏好>决策>判断>风险（investment_research.py）。
    统一卡片模板：色块标题条 + 白底主体 + 同色细边框；字多模块大、字少模块小。"""
    paths = [("词法路", "pg_trgm 相似度", "subject 与 content 三元组命中", "配额 40%"),
             ("向量路", "embedding 余弦", "语义近义，Qwen 向量化", "配额 30%"),
             ("近期路", "observed_at 补额", "补足配额，按时间排序", "配额 30%")]
    prio = ["偏好 > 决策 > 判断 > 风险", "仅召回 active 记忆", "关系链追一层防漂移", "过期 / 撤销不召回"]

    # —— 统一配色：全流程 navy 主色，标题条深浅分阶段，主体统一白底 + navy 细边框 ——
    # 标题条色：端点用 navy，核心（三路/打分）用 darknavy 加深以突出
    HEAD_LIGHT = C["navy"]      # 端点模块标题条
    HEAD_DEEP = C["darknavy"]   # 核心模块标题条（加深突出）
    EDGE = C["navy"]            # 所有主体边框统一 navy

    # 统一卡片模板：色块标题条 + 白底主体（navy 细边框）。fill=True 撑满父列
    def card(title, sub, head_color, body_html, fill=False):
        return (f"<div style='display:flex;flex-direction:column;{('height:100%;min-height:0;' if fill else '')}'>"
                f"<div style='background:{head_color};color:#fff;border-radius:8px 8px 0 0;padding:9px 8px;text-align:center;flex:0 0 auto;'>"
                f"<div style='font-size:16px;font-weight:800;line-height:1.2;'>{title}</div>"
                f"<div style='font-size:12px;opacity:0.92;margin-top:3px;line-height:1.2;'>{sub}</div></div>"
                f"<div style='background:#fff;border:1px solid {EDGE};border-top:none;border-radius:0 0 8px 8px;padding:12px 10px;{('flex:1 1 0;min-height:0;' if fill else 'flex:0 0 auto;')}display:flex;flex-direction:column;justify-content:center;text-align:center;align-items:stretch;'>"
                f"{body_html}</div></div>")

    # 用户问题（端点）
    q = card("本轮问题", "检索起点", HEAD_LIGHT,
             f"<div style='font-size:17px;font-weight:800;color:{C['ink']};line-height:1.4;'>用户提问</div>", fill=True)
    # 身份过滤（端点）
    f_ = card("身份过滤", "owner 隔离", HEAD_LIGHT,
              f"<div style='font-size:14px;font-weight:700;color:{C['ink']};line-height:1.55;'>先按 owner 过滤<br/>团队记忆可见</div>", fill=True)
    # 三路召回（核心，纵向三子卡——统一 pblue 底 + navy 边框，用编号区分）
    path_cards = ""
    for i, (n, method, desc, quota) in enumerate(paths, 1):
        path_cards += (f"<div style='flex:1;background:{C['pblue']};border:1px solid {EDGE};border-radius:8px;padding:9px 10px;display:flex;flex-direction:column;justify-content:center;text-align:center;min-height:0;'>"
                       f"<div style='font-size:15px;font-weight:800;color:{C['navy']};line-height:1.2;'>{i}. {n}</div>"
                       f"<div style='font-size:12.5px;color:{C['ink']};margin-top:4px;line-height:1.3;'>{method}</div>"
                       f"<div style='font-size:11.5px;color:{C['mid']};margin-top:3px;line-height:1.3;'>{desc}</div>"
                       f"<div style='font-size:13px;font-weight:700;color:{C['navy']};margin-top:5px;'>{quota}</div></div>")
    three_body = f"<div style='display:flex;flex-direction:column;gap:9px;flex:1;min-height:0;'>{path_cards}</div>"
    three = card("三路混合召回", "词法 · 向量 · 近期", HEAD_DEEP, three_body, fill=True)
    # 打分融合（核心，关系加权为召回后加成框 + 主框居中填满）
    merge_body = (
        f"<div style='display:flex;flex-direction:column;flex:1;gap:9px;'>"
        # 关系加权（召回后加权，非独立路——浅底虚线框区分，仅此一处点缀色）
        f"<div style='background:{C['pgreen']};border:1px dashed {C['green']};border-radius:6px;padding:8px 6px;text-align:center;'>"
        f"<div style='font-size:13px;font-weight:800;color:{C['green']};'>关系加权 +0.12</div>"
        f"<div style='font-size:11px;color:{C['mid']};margin-top:2px;'>supports/challenges 追一层</div></div>"
        # 主框：合并去重·加权打分（浅底 navy 边框，与全流程统一）
        f"<div style='background:{C['vly']};border:1px solid {EDGE};border-radius:8px;padding:14px 8px;text-align:center;flex:1;display:flex;flex-direction:column;justify-content:center;'>"
        f"<div style='font-size:16px;font-weight:800;line-height:1.35;color:{C['navy']};'>合并去重</div>"
        f"<div style='font-size:16px;font-weight:800;line-height:1.35;margin-top:5px;color:{C['navy']};'>加权打分</div>"
        f"<div style='font-size:12px;color:{C['mid']};margin-top:10px;'>阈值 ≥ 0.18</div></div></div>")
    merge = card("打分融合", "召回后加权", HEAD_DEEP, merge_body, fill=True)
    # top-K 截断（端点）
    topk = card("top-K 截断", "token 预算内", HEAD_LIGHT,
                f"<div style='font-size:14.5px;font-weight:700;color:{C['navy']};line-height:1.7;'>按分排序<br/>取前 K 条</div>"
                f"<div style='font-size:12.5px;color:{C['mid']};margin-top:10px;line-height:1.6;'>超预算低分条<br/>截断丢弃</div>", fill=True)
    # 注入（端点）
    inj = card("注入上下文", "打分高者优先", HEAD_LIGHT,
               f"<div style='font-size:16px;font-weight:800;color:{C['ink']};line-height:1.45;'>注入模型上下文</div>"
               f"<div style='font-size:12.5px;color:{C['mid']};margin-top:10px;line-height:1.6;'>打分高者优先进 LLM</div>", fill=True)

    arrow = f"<div style='display:flex;align-items:center;color:{C['mid']};font-size:24px;font-weight:700;flex:0 0 auto;padding:0 3px;'>→</div>"

    # 六列 grid 网格，1fr 等高；箭头单独成列窄宽
    cols = "1fr 26px 1.1fr 26px 2.4fr 26px 1.4fr 26px 1fr 26px 1.5fr"
    flow = (f"<div style='display:grid;grid-template-columns:{cols};align-items:stretch;height:378px;'>"
            f"<div style='display:flex;flex-direction:column;'>{q}</div>"
            f"<div style='display:flex;align-items:center;justify-content:center;color:{C['mid']};font-size:24px;font-weight:700;'>→</div>"
            f"<div style='display:flex;flex-direction:column;'>{f_}</div>"
            f"<div style='display:flex;align-items:center;justify-content:center;color:{C['mid']};font-size:24px;font-weight:700;'>→</div>"
            f"<div style='display:flex;flex-direction:column;'>{three}</div>"
            f"<div style='display:flex;align-items:center;justify-content:center;color:{C['mid']};font-size:24px;font-weight:700;'>→</div>"
            f"<div style='display:flex;flex-direction:column;'>{merge}</div>"
            f"<div style='display:flex;align-items:center;justify-content:center;color:{C['mid']};font-size:24px;font-weight:700;'>→</div>"
            f"<div style='display:flex;flex-direction:column;'>{topk}</div>"
            f"<div style='display:flex;align-items:center;justify-content:center;color:{C['mid']};font-size:24px;font-weight:700;'>→</div>"
            f"<div style='display:flex;flex-direction:column;'>{inj}</div>"
            f"</div>")

    body = f"""
    <div style='padding:12px 28px 0 28px;'>
      <div class='h2'>召回：词法 + 向量 + 近期三路混合，召回后关系加权</div>
      <div class='sub' style='margin-top:3px;'>词法管字面命中、向量管语义近义、近期补足时间新近度——三路去重合并后打分排序，关系在召回后对候选加权而非独立召回</div>
    </div>
    <div style='padding:10px 28px 0 28px;'>{flow}</div>
    <div style='position:absolute;bottom:12px;left:28px;right:28px;display:flex;gap:14px;'>
      <div style='flex:1.2;background:{C['vly']};border:1px solid {C['light']};border-radius:8px;padding:10px 14px;'>
        <div style='font-size:14.5px;font-weight:800;color:{C['navy']};'>召回优先级（投研 Profile 可调）</div>
        <div style='display:flex;flex-wrap:wrap;gap:6px 24px;margin-top:7px;'>
          {''.join(f"<div style='font-size:13px;color:{C['ink']};'><span style='color:{C['blue']};'>●</span>  {p}</div>" for p in prio)}
        </div>
      </div>
      <div style='flex:1;background:{C['pblue']};border-radius:8px;padding:10px 14px;'>
        <div style='font-size:14.5px;font-weight:800;color:{C['navy']};'>打分加成常量</div>
        <div style='font-size:12.5px;color:{C['ink']};margin-top:7px;line-height:1.65;'>subject 精确命中 +0.20 · 向量相似度 +0.15 · profile 提示词 +0.16 · 关系加成 +0.12 · 时效衰减 0.15（半衰期 90 天）</div>
      </div>
    </div>"""
    return _page(body)


# ===== P13 生命周期（状态机：active→三终态 + 槽位释放）=====
def lifecycle():
    """记忆生命周期状态机。严格对照 schema CHECK(0001_memory_schema.sql:22-23) 4 状态：
    active/superseded/expired/revoked。转换：
    - active→superseded: replacement，旧 revision 留 is_current=FALSE+superseded，新版 active (repository.py:1090-1113)
    - active→revoked: revoke_memory 工具，释放唯一索引槽位、活动边 stale，幂等 (repository.py:618-675)
    - active→expired: maintenance 周期物化 valid_until<=now (maintenance.py:30-62)
    召回只查 active (recall.py:286)。superseded/revoked/expired 均终态(design.md §8.1)。
    槽位释放：唯一索引 WHERE lifecycle_status='active' (schema:41-43)，revoke 后可新建 active，非旧记忆复活。"""
    # HTML grid：左 active（跨三行）| 带规则的转移边 | 终态框
    # 统一配色：navy 同色系深浅区分（active 深 navy、终态 navy 深浅两档 + 浅底），
    # 不用 slate/wine/teal 三种杂色，靠深浅 + 浅底填充区分模块，沉稳不乱
    NAVY = C["navy"]        # active 深主色
    TERM = C["blue"]        # 终态统一主色（navy 浅一档同色系）
    TERM_LT = C["deepnavy"] # 终态标题条可选加深
    # active 主块（navy，跨三行填满）
    active_blk = (
        f"<div style='grid-row:1/span 3;grid-column:1;display:flex;flex-direction:column;'>"
        f"<div style='background:{NAVY};border-radius:12px;padding:22px 16px;flex:1 1 0;min-height:0;display:flex;flex-direction:column;justify-content:space-between;'>"
        f"<div style='text-align:center;'>"
        f"<div style='font-size:27px;font-weight:800;color:#fff;letter-spacing:1px;'>active</div>"
        f"<div style='font-size:14.5px;font-weight:700;color:{C['pblue']};margin-top:4px;'>有效态</div>"
        f"</div>"
        f"<div style='border-top:1px solid {C['pblue']};opacity:0.4;margin:8px 4px;'></div>"
        f"<div>"
        f"<div style='font-size:12.5px;font-weight:700;color:#fff;margin-bottom:5px;'>召回作用域</div>"
        f"<div style='font-size:11.5px;color:{C['pblue']};line-height:1.7;'>仅有效态注入模型上下文<br/>其余三态退场、不参与召回</div>"
        f"</div>"
        f"<div style='border-top:1px solid {C['pblue']};opacity:0.4;margin:6px 4px;'></div>"
        f"<div>"
        f"<div style='font-size:12.5px;font-weight:700;color:#fff;margin-bottom:5px;'>不变量约束</div>"
        f"<div style='font-size:11.5px;color:{C['pblue']};line-height:1.7;'>(主题, 类型) 唯一索引<br/>valid_until 控制时效边界</div>"
        f"</div>"
        f"<div style='border-top:1px solid {C['pblue']};opacity:0.4;margin:6px 4px;'></div>"
        f"<div>"
        f"<div style='font-size:12.5px;font-weight:700;color:#fff;margin-bottom:5px;'>转换可追溯</div>"
        f"<div style='font-size:11.5px;color:{C['pblue']};line-height:1.7;'>三条转换均留版本记录<br/>历史不丢 · 槽位释放可重建</div>"
        f"</div>"
        f"</div></div>"
    )
    # 三行转移：每行 = 带规则的转移边（col2） + 终态框（col3）
    rows = [
        dict(name="superseded", cn="已取代",
             op="判断修订", op_code="replacement",
             rule="用户改判断 → capture 识别修订语义 → 旧版归档、新版上位",
             trig="触发词：改成 / 调整 / 不再持有",
             mech="旧版置为非当前（is_current=否）并标记已取代；新版成为当前有效态",
             recall="不再召回，旧版保留供演进追溯",
             ex="例：看多 IP 判断被新版取代",
             extra=""),
        dict(name="revoked", cn="已撤销",
             op="显式撤销", op_code="revoke_memory",
             rule="用户显式调用撤销工具，操作幂等、可重复执行",
             trig="触发：误录入 / 失效 / 无效判断",
             mech="释放唯一索引槽位，关联关系标记失效；保留记录供审计",
             recall="不再召回，保留完整审计痕迹",
             ex="例：撤销误入库的错误判断",
             extra=f"<div style='margin-top:6px;padding:4px 8px;background:{C['pgreen']};border-radius:6px;border:1px dashed {C['green']};'><span style='color:{C['green']};font-size:11.5px;font-weight:700;'>↻ 槽位释放后可新建有效态（非旧记忆复活）</span></div>"),
        dict(name="expired", cn="已到期",
             op="到期物化", op_code="maintenance 周期",
             rule="有效期届满（valid_until ≤ 当前时间），维护循环自动物化失效",
             trig="触发：valid_until 到期",
             mech="版本与条目同步置为已到期，关联关系标记失效",
             recall="不再召回，不污染模型上下文",
             ex="例：旺季判断到期自动退场",
             extra=""),
    ]
    cells = [active_blk]
    for i, r in enumerate(rows):
        gc = i + 1
        # 带规则的转移边：横线 + 操作名徽章 + 箭头，下方写触发规则
        cells.append(
            f"<div style='grid-row:{gc};grid-column:2;display:flex;flex-direction:column;justify-content:center;padding:0 2px;'>"
            f"<div style='display:flex;align-items:center;'>"
            f"<div style='flex:1;height:0;border-top:2.5px solid {TERM};'></div>"
            f"<div style='background:{TERM};color:#fff;font-size:13.5px;font-weight:800;padding:5px 12px;border-radius:15px;white-space:nowrap;'>{r['op']}</div>"
            f"<div style='flex:1;height:0;border-top:2.5px solid {TERM};'></div>"
            f"<div style='color:{TERM};font-size:24px;font-weight:800;line-height:1;margin-left:2px;'>▶</div>"
            f"</div>"
            f"<div style='text-align:center;margin-top:9px;font-size:11.5px;color:{C['ink']};font-weight:600;line-height:1.5;'>{r['rule']}</div>"
            f"<div style='text-align:center;margin-top:4px;font-size:10.5px;color:{C['mid']};line-height:1.45;'>{r['trig']}</div>"
            f"<div style='text-align:center;margin-top:3px;'><span style='font-size:10px;color:{NAVY};font-weight:700;opacity:0.75;'>{r['op_code']}</span></div>"
            f"</div>"
        )
        # 终态框（浅底 navy 边，统一配色）
        cells.append(
            f"<div style='grid-row:{gc};grid-column:3;display:flex;flex-direction:column;'>"
            f"<div style='background:{TERM};border-radius:8px 8px 0 0;padding:8px 14px;display:flex;align-items:baseline;gap:9px;flex:0 0 auto;'>"
            f"<span style='font-size:18px;font-weight:800;color:#fff;letter-spacing:0.5px;'>{r['name']}</span>"
            f"<span style='font-size:13px;font-weight:700;color:#fff;opacity:0.95;'>{r['cn']}</span></div>"
            f"<div style='background:{C['pblue']};border:1.5px solid {TERM};border-top:none;border-radius:0 0 8px 8px;padding:9px 14px;flex:1 1 0;min-height:0;display:flex;flex-direction:column;justify-content:center;'>"
            f"<div style='display:flex;align-items:baseline;gap:7px;'><span style='color:{NAVY};font-size:11.5px;font-weight:800;flex:0 0 auto;'>机制</span><span style='font-size:11.5px;color:{C['ink']};line-height:1.55;'>{r['mech']}</span></div>"
            f"<div style='display:flex;align-items:baseline;gap:7px;margin-top:5px;'><span style='color:{NAVY};font-size:11.5px;font-weight:800;flex:0 0 auto;'>召回</span><span style='font-size:11.5px;color:{C['ink']};line-height:1.55;'>{r['recall']}</span></div>"
            f"<div style='font-size:10.5px;color:{C['mid']};margin-top:5px;'>{r['ex']}</div>"
            f"{r['extra']}"
            f"</div></div>"
        )
    grid_html = "".join(cells)
    body = f"""
    <div style='padding:14px 28px 0 28px;'>
      <div class='h2'>生命周期：判断能改、能废、能过期——但都不丢历史</div>
      <div class='sub' style='margin-top:3px;'>四状态机：有效态唯一参与召回，已取代 / 已撤销 / 已到期均退场不召回；撤销释放唯一索引槽位后允许新建有效态，而非旧记忆复活</div>
    </div>
    <div style='position:absolute;top:88px;bottom:14px;left:28px;right:28px;'>
      <div style='display:grid;grid-template-columns:215px 1fr 1.4fr;grid-template-rows:1fr 1fr 1fr;gap:11px 0;height:100%;'>
        {grid_html}
      </div>
    </div>"""
    return _page(body)


# ===== P14 团队流程 =====
def team_flow():
    """团队公共记忆自动提取流程。严格对照 design.md §5.5（行412-433）：
    触发：_run_team_extraction_loop 周期 3600s（默认，0 关闭）
    聚类：按 memory_type 分组 → 组内 embedding 余弦(阈值0.70)贪心聚类，最小簇2
    簇门槛：≥2 不同成员防回声室；对立 business_progress(resolved/invalidated)丢弃=弱方向校验
    候选向量：簇内成员均值(簇中心)，稳定不漂移
    产出：共性候选写团队 owner 的 pending review
    隔离：只读成员个人记忆、只写团队公共空间
    幂等：同 subject+type 已有 pending 或 confirmed 不重复；embedding 余弦<0.05 检测语义重复
    确认：人工 confirm，不做自动确认。owner_key = tenant:team:team_id，全员可见"""
    NAVY = C["navy"]
    BLUE = C["blue"]
    # 上方流程节点白底，靠边框色 navy(端点) / blue(中间步骤) 区分类型
    NODE_BG = "#ffffff"
    DETAIL_BG = C["pblue"]  # 下方细则卡浅蓝底，与上方白底形成层次区分
    # 主流程节点（宽矮，标题+一行说明）
    def node(title, sub, border):
        return (f"<div style='background:{NODE_BG};border:1.5px solid {border};border-radius:10px;padding:15px 12px;text-align:center;display:flex;flex-direction:column;justify-content:center;'>"
                f"<div style='font-size:18px;font-weight:800;color:{border};line-height:1.2;'>{title}</div>"
                f"<div style='font-size:13px;color:{C['mid']};margin-top:7px;line-height:1.55;'>{sub}</div></div>")
    # 带标签的干净箭头：标签在上，线 + 三角箭头在下
    def arrow(label):
        return (f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;'>"
                f"<div style='font-size:11.5px;color:{NAVY};font-weight:700;white-space:nowrap;letter-spacing:0.5px;'>{label}</div>"
                f"<div style='display:flex;align-items:center;width:100%;'>"
                f"<div style='flex:1;height:2.5px;background:{BLUE};'></div>"
                f"<div style='width:0;height:0;border-left:12px solid {BLUE};border-top:7px solid transparent;border-bottom:7px solid transparent;'></div>"
                f"</div></div>")
    # 细则卡：标题 + 条目 + 底注，三段用 space-between 撑满高度，字体放大间距拉开
    def detail(col, title, items, footer):
        lis = "".join(f"<div style='font-size:13px;color:{C['ink']};line-height:1.75;margin-top:8px;'><span style='color:{NAVY};font-weight:700;'>{k}</span> {v}</div>" for k, v in items)
        return (f"<div style='grid-column:{col};background:{DETAIL_BG};border:1px solid {C['light']};border-radius:8px;padding:12px 15px;display:flex;flex-direction:column;justify-content:space-between;'>"
                f"<div style='font-size:15px;font-weight:800;color:{NAVY};'>{title}</div>"
                f"<div>{lis}</div>"
                f"<div style='font-size:12px;color:{C['mid']};line-height:1.6;border-top:1px solid {C['light']};padding-top:8px;margin-top:8px;'>{footer}</div>"
                f"</div>")
    # 同一 7 列网格：节点在 1/3/5/7，箭头在 2/4/6
    COLS = "1fr 78px 1.35fr 78px 1fr 78px 1fr"
    flow = (
        f"<div style='display:grid;grid-template-columns:{COLS};align-items:stretch;gap:0 0;'>"
        f"{node('成员个人记忆', '各自捕获落库 · 互相隔离<br/>归属键 tenant:subject-编号', NAVY)}"
        f"<div style='grid-column:2;'>{arrow('周期扫描')}</div>"
        f"{node('周期聚类 · 簇校验', '服务端后台周期扫描成员记忆<br/>向量相似度 ≥ 0.70 逐步归并', BLUE)}"
        f"<div style='grid-column:4;'>{arrow('通过校验')}</div>"
        f"{node('团队待确认候选', '共性候选写入团队归属 · 待审<br/>幂等 + 语义去重防重复', BLUE)}"
        f"<div style='grid-column:6;'>{arrow('人工确认')}</div>"
        f"{node('团队公共记忆', '任一成员确认即转正 · 全员可见<br/>归属键 tenant:team:编号', NAVY)}"
        f"</div>"
    )
    # 细则卡用同一 7 列，放在 1/3/5/7，严格对齐节点
    details = (
        f"<div style='display:grid;grid-template-columns:{COLS};gap:0 0;height:100%;'>"
        f"{detail(1, '成员个人记忆', [('落库', '捕获 → 准入 → 归个人归属'), ('归属键', 'tenant:subject-编号'), ('可见性', '只自己可见，互相隔离')], '提取阶段只读，个人记忆不被改变')}"
        f"{detail(3, '聚类与校验', [('触发', '周期循环（默认每小时，可关闭）'), ('分组', '按记忆类型分组后聚类'), ('相似度', '向量余弦 ≥ 0.70'), ('归并', '逐步归并，最少 2 条成簇'), ('防回声', '需来自 2 个不同成员'), ('防对立', '立场相反（看多/看空）丢弃'), ('候选向量', '取簇内成员均值，代表性强')], '不调用大模型合成，原文留存供人审阅')}"
        f"{detail(5, '候选与确认', [('产出', '共性候选写入团队归属待审'), ('幂等', '同主题+类型已有待审或已确认不重复'), ('语义去重', '向量余弦距离 < 0.05 判重'), ('确认', '人工确认后转正')], '不做自动确认，由人决定是否沉淀')}"
        f"{detail(7, '团队公共记忆', [('归属键', 'tenant:team:编号'), ('可见性', '按可见归属集合召回'), ('受众', '同租户下同团队成员')], '个人与团队靠 team: 中缀命名空间隔离，互不可写')}"
        f"</div>"
    )
    body = f"""
    <div style='padding:14px 28px 0 28px;'>
      <div class='h2'>团队记忆：从个人共识到团队共识</div>
      <div class='sub' style='margin-top:3px;'>周期性聚类多个人的相似判断，自动提议共性候选，一人确认即沉淀为团队公共记忆——全程不做自动确认，人决定共识</div>
    </div>
    <div style='position:absolute;top:86px;left:28px;right:28px;height:118px;'>{flow}</div>
    <div style='position:absolute;top:220px;bottom:14px;left:28px;right:28px;'>{details}</div>"""
    return _page(body)


# ===== P15 身份隔离（派生链居中 + 三卡环绕）=====
def isolation():
    """身份隔离机制。严格对照 design.md §5.1-5.4（行360-410）：
    派生链：Bearer Token → StaticTokenVerifier → claims(tenant_id/subject_id/team_ids)
            → owner_key=tenant_id:subject_id / team:team_id → PrincipalContext → visible_owner_ids
    双重隔离(§5.2)：认证(Bearer≥32字符)+授权(MemoryScope read/write/review)+存储(owner_id/visible_owner_ids 过滤)
    命名空间隔离(§5.1)：个人 tenant_id:subject_id、团队 tenant_id:team:team_id，靠 team: 中缀隔离
    可见性(§5.4)：visible_owner_ids=(owner_id,*team_owner_ids)，非成员 owner 不在集合=等同不存在
    铁律2(CLAUDE.md)：工具参数不接受 owner，PrincipalContext 由 auth.py 从已验证 Token 派生
    布局：左纵向派生链（4层▼串联），右三张细则卡竖排，底铁律条置底"""
    NAVY = C["navy"]
    BLUE = C["blue"]
    DETAIL_BG = C["pblue"]
    # 纵向派生链节点（白底，固定高度 88px，端点 navy / 中间步骤 blue）
    def vnode(title, sub, border):
        return (f"<div style='background:#fff;border:1.5px solid {border};border-radius:10px;padding:9px 14px;height:78px;flex:0 0 78px;display:flex;flex-direction:column;justify-content:center;'>"
                f"<div style='font-size:16px;font-weight:800;color:{border};line-height:1.2;'>{title}</div>"
                f"<div style='font-size:12.5px;color:{C['mid']};margin-top:5px;line-height:1.5;'>{sub}</div></div>")
    # 细则卡：标题 + 条目 + 底注，height:100% 撑满外层固定高度容器
    def detail(title, items, footer):
        lis = "".join(f"<div style='font-size:12.5px;color:{C['ink']};line-height:1.65;margin-top:5px;'><span style='color:{NAVY};font-weight:700;'>{k}</span> {v}</div>" for k, v in items)
        return (f"<div style='background:{DETAIL_BG};border:1px solid {C['light']};border-radius:8px;padding:10px 14px;height:100%;box-sizing:border-box;display:flex;flex-direction:column;justify-content:space-between;overflow:hidden;'>"
                f"<div style='font-size:14px;font-weight:800;color:{NAVY};'>{title}</div>"
                f"<div>{lis}</div>"
                f"<div style='font-size:11.5px;color:{C['mid']};line-height:1.5;border-top:1px solid {C['light']};padding-top:6px;margin-top:6px;'>{footer}</div>"
                f"</div>")
    # 左侧纵向派生链：4 节点 + ▼ 箭头，撑满高度
    layers = [
        ('认证令牌', 'Bearer Token ≥ 32 字符<br/>HTTP 头传入 · 不可伪造', NAVY),
        ('身份上下文', '服务端从令牌派生身份<br/>租户/主体/团队标识', BLUE),
        ('归属键 owner_key', '个人 tenant:主体<br/>团队 tenant:team:编号', BLUE),
        ('存储行级隔离', '读写按归属过滤<br/>跨归属猜中编号等同不存在', NAVY),
    ]
    chain = ""
    for i, (t, s, c) in enumerate(layers):
        chain += vnode(t, s, c)
        if i < 3:
            chain += f"<div style='text-align:center;color:{BLUE};font-size:16px;font-weight:800;line-height:1;flex:0 0 auto;'>▼</div>"
    # 右侧三张细则卡：固定高度，不靠 flex/grid 拉伸，避免溢出
    d1 = detail('三级隔离范围', [('租户级', '不同租户数据互不可见'), ('团队级', '团队成员共享，跨团队不可见'), ('个人级', '只自己可见，连队友都不可见')], '个人 tenant:subject / 团队 tenant:team 靠 team: 中缀命名空间隔离')
    d2 = detail('防伪造不变量', [('参数拒收归属', '工具不接受 owner 参数，只从令牌派生'), ('召回先过滤', '按可见归属集合过滤，不先搜后过滤'), ('撤销留痕', '不物理删除，全程审计可追溯')], '铁律：owner 只来自认证上下文，不接受调用方传入')
    d3 = detail('可见性规则', [('可见集合', '本人归属 + 所属团队归属'), ('非成员', '归属不在集合内，等同不存在'), ('团队记忆', '同租户同团队成员可召回')], '写入用记录实际归属，非调用者个人归属')
    body = f"""
    <div style='padding:14px 28px 0 28px;'>
      <div class='h2'>身份隔离：服务端强制，不靠客户端自觉</div>
      <div class='sub' style='margin-top:3px;'>四层派生链从令牌到存储逐级收敛，工具参数不接受归属——从源头防伪造，跨归属猜中编号等同不存在</div>
    </div>
    <div style='position:absolute;top:80px;bottom:14px;left:28px;right:28px;display:grid;grid-template-columns:1fr 1.25fr;gap:24px;overflow:hidden;'>
      <div style='display:flex;flex-direction:column;overflow:hidden;'>
        <div style='font-size:14px;font-weight:800;color:{NAVY};margin-bottom:10px;flex:0 0 auto;'>四层派生链（逐级收敛）</div>
        <div style='flex:1 1 0;display:flex;flex-direction:column;justify-content:space-between;gap:8px;overflow:hidden;'>{chain}</div>
      </div>
      <div style='display:flex;flex-direction:column;overflow:hidden;'>
        <div style='font-size:14px;font-weight:800;color:{NAVY};margin-bottom:10px;flex:0 0 auto;'>三类规则</div>
        <div style='flex:1 1 0;display:flex;flex-direction:column;gap:12px;overflow:hidden;'>
          <div style='flex:1 1 0;min-height:0;overflow:hidden;'>{d1}</div>
          <div style='flex:1 1 0;min-height:0;overflow:hidden;'>{d2}</div>
          <div style='flex:1 1 0;min-height:0;overflow:hidden;'>{d3}</div>
        </div>
      </div>
    </div>"""
    return _page(body)


# ===== P17 测试场景 + 部署形态 =====
def test_design_full():
    NAVY = C["navy"]; BLUE = C["blue"]; GREEN = C["green"]
    # 上半：测试场景（充实真实细节）
    vars_ = ["同一投资对象（泡泡玛特 9992.HK），不同 owner 隔离",
             "5 个会话，38 轮 capture，全部经 Hook 自动捕获",
             "材料时点固定 2025-04-23，保证可比",
             "覆盖建判断→修订→撤销→团队确认全路径"]
    scenario = f"""
      <div style='display:flex;gap:16px;height:130px;'>
        <div style='flex:1;background:{C['pblue']};border-radius:10px;padding:13px 18px;display:flex;flex-direction:column;'>
          <div style='display:flex;align-items:baseline;gap:8px;'>
            <span style='font-size:16px;font-weight:800;color:{NAVY};'>subject-001</span>
            <span style='font-size:11px;color:{C['mid']};'>团队 research-dept</span>
          </div>
          <div style='font-size:12.5px;color:{C['ink']};line-height:1.55;margin-top:8px;'>从「出海持续性」切入<br/>先讲海外增长，再讲 IP 价值</div>
          <div style='margin-top:auto;font-size:20px;font-weight:800;color:{NAVY};'>22 轮 capture</div>
        </div>
        <div style='flex:1;background:{C['pblue']};border-radius:10px;padding:13px 18px;display:flex;flex-direction:column;'>
          <div style='display:flex;align-items:baseline;gap:8px;'>
            <span style='font-size:16px;font-weight:800;color:{NAVY};'>subject-002</span>
            <span style='font-size:11px;color:{C['mid']};'>团队 research-dept</span>
          </div>
          <div style='font-size:12.5px;color:{C['ink']};line-height:1.55;margin-top:8px;'>从「爆款依赖」切入<br/>盯 IP 集中度 + 毛利率变化</div>
          <div style='margin-top:auto;font-size:20px;font-weight:800;color:{NAVY};'>16 轮 capture</div>
        </div>
        <div style='flex:1;background:{C['pblue']};border-radius:10px;padding:13px 18px;display:flex;flex-direction:column;'>
          <div style='font-size:16px;font-weight:800;color:{NAVY};'>验收变量</div>
          <div style='display:flex;flex-direction:column;gap:4px;margin-top:8px;'>
            {''.join(f"<div style='font-size:11.5px;color:{C['ink']};line-height:1.5;'><span style='color:{NAVY};'>●</span> {v}</div>" for v in vars_)}
          </div>
        </div>
      </div>"""
    # 下半：真实部署形态 + 配置项（Windows→阿里云）
    deploy = f"""
      <svg viewBox='0 0 1148 286' width='1148' height='286' style='display:block;'>
        <defs><marker id='ad' markerWidth='9' markerHeight='9' refX='7' refY='4.5' orient='auto'><path d='M0,0 L9,4.5 L0,9 z' fill='{NAVY}'/></marker></defs>
        <!-- Windows 测试机 -->
        <rect x='0' y='14' width='200' height='258' rx='10' fill='{C['pblue']}' stroke='{NAVY}' stroke-width='1.6' stroke-dasharray='6,3'/>
        <text x='100' y='36' text-anchor='middle' fill='{NAVY}' font-size='14' font-weight='800'>Windows 测试机</text>
        <rect x='14' y='50' width='172' height='78' rx='6' fill='#fff' stroke='{NAVY}' stroke-width='1'/>
        <text x='100' y='74' text-anchor='middle' fill='{NAVY}' font-size='12' font-weight='700'>Claude Code</text>
        <text x='100' y='92' text-anchor='middle' fill='{C['mid']}' font-size='10'>~/.claude/settings.json</text>
        <text x='100' y='108' text-anchor='middle' fill='{C['mid']}' font-size='10'>注册 hooks（召回 + 捕获）</text>
        <text x='100' y='121' text-anchor='middle' fill='{C['mid']}' font-size='10'>UserPromptSubmit / Stop</text>
        <rect x='14' y='138' width='172' height='126' rx='6' fill='{C['vly']}' stroke='{NAVY}' stroke-width='1' stroke-dasharray='3,2'/>
        <text x='100' y='160' text-anchor='middle' fill='{NAVY}' font-size='12' font-weight='700'>memory-mcp-agent</text>
        <text x='100' y='182' text-anchor='middle' fill='{C['mid']}' font-size='10'>hook 环境：</text>
        <text x='100' y='198' text-anchor='middle' fill='{NAVY}' font-size='10'>MEMORY_MCP_URL</text>
        <text x='100' y='214' text-anchor='middle' fill='{NAVY}' font-size='10'>MEMORY_MCP_TOKEN</text>
        <text x='100' y='238' text-anchor='middle' fill='{C['mid']}' font-size='10'>mcp 配置：</text>
        <text x='100' y='254' text-anchor='middle' fill='{NAVY}' font-size='10'>streamableHttp · Bearer</text>
        <!-- 连线 -->
        <line x1='200' y1='143' x2='272' y2='143' stroke='{NAVY}' stroke-width='2' marker-end='url(#ad)'/>
        <text x='236' y='135' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>JSON-RPC/HTTP</text>
        <!-- 阿里云容器 -->
        <rect x='272' y='14' width='876' height='258' rx='12' fill='none' stroke='{C['orange']}' stroke-width='1.4' stroke-dasharray='7,4'/>
        <text x='710' y='36' text-anchor='middle' fill='{C['orange']}' font-size='13' font-weight='800'>阿里云 ECS 开发机（同 VPC）</text>
        <!-- Memory MCP Server -->
        <rect x='288' y='50' width='300' height='208' rx='10' fill='{NAVY}'/>
        <text x='438' y='74' text-anchor='middle' fill='#fff' font-size='14' font-weight='800'>Memory MCP Server</text>
        <text x='438' y='92' text-anchor='middle' fill='#cfe' font-size='11'>:8765/mcp · systemd 管理</text>
        <rect x='300' y='104' width='276' height='54' rx='5' fill='{C['deepnavy']}'/>
        <text x='438' y='123' text-anchor='middle' fill='#9bc' font-size='10'>数据配置</text>
        <text x='438' y='140' text-anchor='middle' fill='#fff' font-size='10.5' font-weight='700'>DATABASE_URL · AUTH_TOKENS</text>
        <text x='438' y='153' text-anchor='middle' fill='#9bc' font-size='9'>(→ tenant / subject / team_ids)</text>
        <rect x='300' y='164' width='276' height='44' rx='5' fill='{C['deepnavy']}'/>
        <text x='438' y='183' text-anchor='middle' fill='#9bc' font-size='10'>模型配置</text>
        <text x='438' y='200' text-anchor='middle' fill='#fff' font-size='10.5' font-weight='700'>MODEL_* · EMBEDDING_*</text>
        <rect x='300' y='214' width='276' height='38' rx='4' fill='{C['deepnavy']}'/>
        <text x='438' y='230' text-anchor='middle' fill='#9bc' font-size='10'>运行特征</text>
        <text x='438' y='245' text-anchor='middle' fill='#fff' font-size='10' font-weight='700'>连接池 · 三个 worker 循环</text>
        <!-- 连线 server->后端 -->
        <line x1='588' y1='154' x2='634' y2='154' stroke='{NAVY}' stroke-width='2' marker-end='url(#ad)'/>
        <text x='611' y='147' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>适配层</text>
        <!-- PostgreSQL RDS -->
        <rect x='634' y='50' width='232' height='208' rx='10' fill='#fff' stroke='{NAVY}' stroke-width='1.6'/>
        <text x='750' y='74' text-anchor='middle' fill='{NAVY}' font-size='13' font-weight='800'>PostgreSQL</text>
        <text x='750' y='92' text-anchor='middle' fill='{C['mid']}' font-size='10.5'>阿里云 RDS · VPC 私网</text>
        <rect x='646' y='104' width='208' height='40' rx='4' fill='{C['vly']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='750' y='120' text-anchor='middle' fill='{NAVY}' font-size='10.5' font-weight='700'>唯一权威存储</text>
        <text x='750' y='136' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>跨 owner 猜中编号等同不存在</text>
        <rect x='646' y='150' width='208' height='40' rx='4' fill='{C['vly']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='750' y='166' text-anchor='middle' fill='{NAVY}' font-size='10.5' font-weight='700'>行级隔离</text>
        <text x='750' y='182' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>读写按 owner_id 过滤</text>
        <rect x='646' y='196' width='208' height='56' rx='4' fill='{C['pblue']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='750' y='216' text-anchor='middle' fill='{NAVY}' font-size='11' font-weight='700'>10 表 · 26 索引</text>
        <text x='750' y='236' text-anchor='middle' fill='{NAVY}' font-size='11' font-weight='700'>72 CHECK 约束</text>
        <!-- LLM/Embedding MAAS -->
        <rect x='886' y='50' width='232' height='208' rx='10' fill='#fff' stroke='{NAVY}' stroke-width='1.6'/>
        <text x='1002' y='74' text-anchor='middle' fill='{NAVY}' font-size='13' font-weight='800'>LLM / Embedding</text>
        <text x='1002' y='92' text-anchor='middle' fill='{C['mid']}' font-size='10.5'>阿里云 MAAS（北京）</text>
        <rect x='898' y='104' width='208' height='40' rx='4' fill='{C['vly']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='1002' y='120' text-anchor='middle' fill='{NAVY}' font-size='10.5' font-weight='700'>DeepSeek</text>
        <text x='1002' y='136' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>对话记忆结构化抽取</text>
        <rect x='898' y='150' width='208' height='40' rx='4' fill='{C['vly']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='1002' y='166' text-anchor='middle' fill='{NAVY}' font-size='10.5' font-weight='700'>Qwen text-embedding-v3</text>
        <text x='1002' y='182' text-anchor='middle' fill='{C['mid']}' font-size='9.5'>语义向量化（召回用）</text>
        <rect x='898' y='196' width='208' height='56' rx='4' fill='{C['pblue']}' stroke='{NAVY}' stroke-width='1'/>
        <text x='1002' y='216' text-anchor='middle' fill='{NAVY}' font-size='11' font-weight='700'>温度 0 · 重试 2</text>
        <text x='1002' y='236' text-anchor='middle' fill='{NAVY}' font-size='11' font-weight='700'>cn-beijing.maas.aliyuncs.com</text>
      </svg>"""
    body = f"""
    <div style='padding:14px 30px 0 30px;'>
      <div class='h2' style='font-size:19px;'>测试场景：两人研究同一投资对象，验证身份隔离与团队记忆收敛</div>
      <div class='sub' style='margin-top:2px;'>真实模型抽取 + 真实数据库存储，不同 owner 各自建判断、各自修订撤销，最终收敛到团队共识</div>
      <div style='margin-top:8px;'>{scenario}</div>
    </div>
    <div style='position:absolute;top:232px;left:30px;right:30px;'>
      <div style='font-size:17px;font-weight:800;color:{NAVY};margin-bottom:6px;'>真实部署形态：测试机接入，记忆服务部署在阿里云开发机</div>
      {deploy}
    </div>"""
    return _page(body)


# ===== P18 演示 =====
def _demo_contrast_page(title, condition, left_one, right_one):
    """对照页：顶部标题 → 左右对话截图占位（撑满到底，无 DB 条）。
    要点压成一句，截图区留空，画布绝大部分留给截图。"""
    NAVY = C["navy"]
    body = f"""
    <div style='padding:20px 30px 0 30px;display:flex;align-items:baseline;justify-content:space-between;gap:24px;'>
      <div class='h2' style='font-size:21px;'>{title}</div>
      <div class='sub' style='font-size:12.5px;white-space:nowrap;'>{condition}</div>
    </div>
    <div style='position:absolute;top:74px;left:30px;right:30px;bottom:20px;display:flex;gap:16px;'>
      <div style='flex:1;background:{C['vly']};border:1.5px dashed {C['light']};border-radius:10px;padding:12px 16px;display:flex;flex-direction:column;'>
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px;'>
          <span style='background:#bbb;color:#fff;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:700;'>无记忆</span>
          <span style='font-size:12px;color:{C['mid']};'>{left_one}</span>
        </div>
        <div style='flex:1;background:#fff;border-radius:6px;'></div>
      </div>
      <div style='flex:1;background:{C['pblue']};border:1.5px solid {NAVY};border-radius:10px;padding:12px 16px;display:flex;flex-direction:column;'>
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px;'>
          <span style='background:{NAVY};color:#fff;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:700;'>有记忆</span>
          <span style='font-size:12px;color:{NAVY};'>{right_one}</span>
        </div>
        <div style='flex:1;background:#fff;border-radius:6px;'></div>
      </div>
    </div>"""
    return _page(body)


def demo_contrast_continue():
    """P19 延续性对照"""
    return _demo_contrast_page(
        "延续性：新开会话能不能从上次判断继续",
        "同一投资对象，新开一个会话再问",
        "每次从零开始，泛泛而谈",
        "召回上次判断，延续推理",
    )


def _demo_scene_page(title, condition, one_liner, db_label):
    """场景页：顶部标题 → 左对话截图 + 右 DB 截图（左右撑满到底，截图区留空）。"""
    NAVY = C["navy"]
    body = f"""
    <div style='padding:20px 30px 0 30px;display:flex;align-items:baseline;justify-content:space-between;gap:24px;'>
      <div class='h2' style='font-size:21px;'>{title}</div>
      <div class='sub' style='font-size:12.5px;white-space:nowrap;'>{condition}</div>
    </div>
    <div style='position:absolute;top:74px;left:30px;right:30px;bottom:20px;display:flex;gap:16px;'>
      <div style='flex:1.15;background:{C['pblue']};border:1.5px solid {NAVY};border-radius:10px;padding:12px 16px;display:flex;flex-direction:column;'>
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px;'>
          <span style='background:{NAVY};color:#fff;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:700;'>对话</span>
          <span style='font-size:12px;color:{NAVY};'>{one_liner}</span>
        </div>
        <div style='flex:1;background:#fff;border-radius:6px;'></div>
      </div>
      <div style='flex:1;background:#fff;border:1px solid {NAVY};border-radius:10px;padding:12px 16px;display:flex;flex-direction:column;'>
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px;'>
          <span style='background:{NAVY};color:#fff;border-radius:3px;padding:2px 8px;font-size:11px;font-weight:700;'>DB</span>
          <span style='font-size:12px;color:{NAVY};'>{db_label}</span>
        </div>
        <div style='flex:1;background:#fff;border:1px solid {C['light']};border-radius:6px;'></div>
      </div>
    </div>"""
    return _page(body)


def demo_scene_revise():
    """P20 判断演进场景"""
    return _demo_scene_page(
        "判断演进：改判断，记忆怎么跟着变",
        "用户中途修正投资判断",
        "用户改判断 A→B，capture 自动 replacement，旧版保留",
        "memory_revisions 表：每次修订留痕",
    )


def demo_scene_converge():
    """P21 跨人收敛场景"""
    return _demo_scene_page(
        "跨人收敛：两人各建各的，能否收敛到团队共识",
        "subject-001 / subject-002 各自建判断",
        "两人各建判断、互相可见，收敛后 confirm 成团队记忆",
        "团队记忆 + confirm 记录",
    )


# ===== P23 总结 =====
def summary_full():
    """P23 总结：上工作量(3) + 下优化点(3) + 底部项目地址页脚条"""
    NAVY = C["navy"]
    work = [
        ("10", "张数据表", "记忆身份·版本·证据·审查·关系\n治理全沉淀在 schema 层"),
        ("13", "个 MCP 工具", "捕获·召回·审查·关系·撤销\n标准协议，换 Agent 不丢记忆"),
        ("2.2 万", "行实现代码", "Server + 轻量 Agent Client\n含 PostgreSQL 适配与异步治理"),
    ]
    wcards = ""
    for n, u, d in work:
        dd = d.replace("\n", "<br>")
        wcards += f"""
        <div style='flex:1;background:{C['pblue']};border-radius:14px;padding:22px 26px;display:flex;flex-direction:column;justify-content:center;gap:14px;'>
          <div style='display:flex;align-items:baseline;gap:8px;'>
            <span style='font-size:42px;font-weight:800;color:{NAVY};line-height:1;'>{n}</span>
            <span style='font-size:17px;font-weight:700;color:{NAVY};'>{u}</span>
          </div>
          <div style='font-size:17px;color:{C['ink']};line-height:1.95;'>{dd}</div>
        </div>"""
    nxt = [
        ("记忆质量闭环", "抽取置信度评分 + 召回未命中信号回流，反哺 prompt 与阈值迭代"),
        ("真实鉴权逻辑", "当前静态 Token，接入 OAuth/JWT 等真实鉴权与租户管理"),
        ("丰富团队功能", "团队权限分级、成员可见域控制、跨团队记忆协作"),
    ]
    nrows = ""
    for h, b in nxt:
        nrows += f"""
        <div style='flex:1;background:#fff;border:1.5px solid {NAVY};border-radius:14px;padding:22px 26px;display:flex;flex-direction:column;justify-content:center;gap:14px;'>
          <div style='font-size:20px;font-weight:800;color:{NAVY};'>{h}</div>
          <div style='font-size:17px;color:{C['ink']};line-height:1.95;'>{b}</div>
        </div>"""
    body = f"""
    <div style='padding:26px 30px 0 30px;'>
      <div class='h2' style='font-size:22px;'>总结：工作量与后续优化</div>
    </div>
    <div style='position:absolute;top:72px;left:30px;right:30px;height:198px;display:flex;gap:16px;'>{wcards}
    </div>
    <div style='position:absolute;top:294px;left:30px;right:30px;'>
      <div style='font-size:18px;font-weight:800;color:{NAVY};margin-bottom:12px;'>后续优化方向</div>
      <div style='display:flex;gap:16px;height:185px;'>{nrows}</div>
    </div>
    <div style='position:absolute;bottom:16px;left:30px;right:30px;background:{NAVY};border-radius:8px;padding:10px 20px;display:flex;align-items:center;justify-content:center;gap:14px;'>
      <span style='font-size:13px;color:#cfe;'>项目地址</span>
      <span style='font-size:15px;font-weight:700;color:#fff;font-family:monospace;letter-spacing:0.3px;'>https://github.com/dxiaosen/memory-mcp</span>
    </div>"""
    return _page(body)


# ===== P21 提问预判 =====
def qa_full():
    qa = [("和 Mem0 区别到底在哪？", "Mem0 是 SDK 嵌入、换 Agent 要重接；Memory MCP 是 MCP 标准协议、零改动接入。而且 Memory MCP 存的是带立场的判断（能被推翻），不是扁平事实；团队记忆能力 Mem0 也没有"),
          ("和 TencentDB Agent Memory 比呢？它也是独立服务", "它是最接近的同行，承认。但它只有资产版本号、无判断间 provenance，团队靠手动共享、无自动共识提取，也无到期 TTL。三轴差异：判断演进审计链 / 团队自动共识 / 失效治理"),
          ("为什么不直接用 RAG 向量库？", "向量只是召回三路之一。记忆需要身份隔离、生命周期、准入、审计这些治理能力，RAG 都没有——它只能算召回，管不了「这条判断还有效吗」"),
          ("去重/聚类阈值怎么定的？", "投研场景调过：replacement 语义 fallback 0.60 + top1-top2 margin 0.08（宁可待确认也不替错）；强匹配 0.75 可豁免歧义保护；echo 去重 0.90"),
          ("多用户会不会串味？", "不会。owner 是服务端从 Token 派生的，工具参数根本不接受 owner（防伪造）；个人和团队多层隔离，互不可写，不靠客户端自觉"),
          ("判断存错了怎么办？", "三种方式：revoke 作废（唯一索引槽位释放，可重建不丢坑）；replacement 取代（旧版保留可追溯）；不物理删除保审计")]
    rows = ""
    for q, a in qa:
        rows += f"""
        <div style='display:flex;gap:14px;align-items:flex-start;background:{C['vly']};border:1px solid {C['light']};border-radius:10px;padding:12px 16px;'>
          <div style='width:40px;height:40px;border-radius:50%;background:{C['red']};color:#fff;font-size:19px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;'>Q</div>
          <div style='flex:1;min-width:0;'>
            <div style='font-size:15.5px;font-weight:700;color:{C['navy']};'>{q}</div>
            <div style='font-size:13px;color:{C['ink']};margin-top:5px;line-height:1.55;'>{a}</div>
          </div>
        </div>"""
    body = f"<div style='padding:20px 28px;display:flex;flex-direction:column;gap:11px;'>{rows}</div>"
    return _page(body)


RENDERERS = {
    # P01 标题页 / P02 目录 / P03 第一章章封 —— 待做
    "P04": pains_full, "P05": why_service,
    "P06": competitor_table, "P07": three_diffs,
    # P08 第二章章封 —— 待做
    "P09": background_full, "P10": memory_model, "P11": lifecycle,
    "P12": admission_full, "P13": recall_three_path,
    "P14": team_flow, "P15": isolation,
    # P17 第三章章封 —— 待做
    "P18": test_design_full,
    "P19": demo_contrast_continue, "P20": demo_scene_revise, "P21": demo_scene_converge,
    # P22 第四章章封 —— 待做
    "P23": summary_full,
}


def render_all():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1.5)
        page = ctx.new_page()
        for name, fn in RENDERERS.items():
            page.set_content(fn(), wait_until="networkidle")
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=False,
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            print(f"{name}.png: {(OUT / f'{name}.png').stat().st_size // 1024}KB")
        browser.close()


if __name__ == "__main__":
    render_all()
