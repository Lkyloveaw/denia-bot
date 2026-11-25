"""Structural guardrails for the dashboard page layout.
    card = _between(text, '<section id="model-pref"', '</section>')
    assert 'id="modelBaseUrl"' in card
    assert 'id="modelApiKey"' in card
    assert 'id="modelProviderSave"' in card
    assert 'id="modelUpstreamFetch"' in card
    assert 'id="modelRestart"' in card
    assert 'fetchModelUpstream' in text and 'saveModelProvider' in text
    assert 'relay' not in card and 'echo-test' not in card

These tests are intentionally about the layout contract, not a specific card's
form. They catch the regressions that would quietly turn the panel back into
one long page, move cards into the overview, restore the 12-column grid, or
drop the mobile hooks.
"""
import re
from pathlib import Path

import dashboard


HTML = Path(dashboard.__file__).with_name('dashboard.html')


def _html():
    return HTML.read_text(encoding='utf-8')


def _between(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def _section_card_ids(fragment):
    ids = []
    for tag in re.findall(r'<section\b[^>]*>', fragment, re.S):
        if not re.search(r'\bclass="[^"]*\bcard\b', tag):
            continue
        match = re.search(r'\bid="([^"]+)"', tag)
        if match:
            ids.append(match.group(1))
    return ids


def _styles(text):
    return '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', text, re.S))


def test_overview_is_separate_from_all_column_cards():
    text = _html()
    overview = _between(text, '<div id="overview"', '<nav id="botBasicsNav"')
    assert 'class="overview-page"' in overview
    assert 'class="hero"' in overview
    assert 'class="kpis"' in overview
    assert _section_card_ids(overview) == []

    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    cards = _section_card_ids(panel)
    assert len(cards) >= 21
    assert len(cards) == len(set(cards))

    blocks = re.split(r'(?=<section\b)', panel)[1:]
    assert blocks
    assert all(re.search(r'<h2\b', block) for block in blocks)


def test_page_head_is_outside_the_pages_and_has_the_main_title():
    text = _html()
    head = _between(text, '<header class="page-head"', '</header>')
    assert re.search(r'<h1 id="pageTitle"', head)
    assert re.search(r'<p class="head-desc" id="pageDesc"', head)

    workspace = text.index('<main class="workspace">')
    overview = text.index('<div id="overview"')
    basics = text.index('<nav id="botBasicsNav"')
    panel = text.index('<div id="panelGrid"')
    footer = text.index('<footer class="page-footer"')
    assert workspace < text.index('<header class="page-head"') < overview < basics < panel < footer


def test_sidebar_links_map_to_real_cards_and_overview():
    text = _html()
    nav = re.search(r'<nav class="nav".*?</nav>', text, re.S).group(0)
    links = re.findall(r'<a [^>]*href="#([^"]+)"', nav)
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    basics_links = re.findall(r'<a [^>]*href="#([^"]+)"', basics)
    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    cards = set(_section_card_ids(panel))

    assert links[0] == 'overview'
    assert len(links) == len(set(links))
    assert len(basics_links) == len(set(basics_links))
    assert set(links) | set(basics_links) == cards | {'overview'}
    assert set(links) - {'overview'} <= cards
    assert set(basics_links) <= cards
    assert re.search(r'<nav id="botBasicsNav"[^>]*\bhidden\b', text)


def test_cards_stay_full_width_and_duplicate_card_titles_are_hidden():
    text = _html()
    styles = _styles(text)
    grid = re.search(r'#panelGrid\{([^}]*)\}', styles)
    assert grid is not None
    rule = grid.group(1)
    assert 'display:block' in rule
    assert 'grid-template-columns' not in rule
    assert 'grid-column' not in rule

    card_rule = re.search(r'#panelGrid > \.card\{([^}]*)\}', styles)
    assert card_rule is not None
    assert not re.search(r'(^|;)width:', card_rule.group(1))

    assert re.search(
        r'body\[data-page="section"\] #panelGrid > \.card:not\(\[hidden\]\) \.card-head h2\{display:none\}',
        styles,
    )

    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    for tag in re.findall(r'<section\b[^>]*>', panel, re.S):
        assert not re.search(r'grid-column|grid-template-columns|\bspan-\d', tag)


def test_model_settings_page_is_wired_to_the_basics_nav_and_loader():
    text = _html()
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    assert 'href="#model-pref"' in basics
    panel = _between(text, '<div id="panelGrid"', '<div id="appearancePopover"')
    assert re.search(r'<section id="model-pref" class="card">', panel)
    assert re.search(r'<h2>模型设置</h2>', panel)
    assert "['#model-pref', loadModelPref]" in text
    assert 'async function loadModelPref' in text
    assert 'id="modelPrimary"' in panel and 'id="modelSecondary"' in panel


def test_bot_basics_is_renamed_tokens_last_and_first_run_guide_is_wired():
    text = _html()
    basics = re.search(r'<nav id="botBasicsNav".*?</nav>', text, re.S).group(0)
    basics_links = re.findall(r'<a [^>]*href="#([^"]+)"', basics)
    assert basics_links == ['bot-info', 'persona', 'model-pref', 'master', 'superusers', 'tokens']
    assert 'aria-label="Bot 基本设置"' in basics
    assert 'Bot 基本设置' in text
    assert 'Bot 基本情况' not in text
    assert 'data-page-title="Bot 基本设置"' in text

    assert 'class="basics-guide-bar"' in text
    assert 'id="basicsGuideNext"' in text
    assert 'id="basicsGuidePrev"' in text
    assert 'id="basicsGuideSkip"' in text
    assert 'basics-guide-steps' not in text
    steps = re.search(r'const basicsSteps=\[(.*?)\];', text, re.S).group(1)
    positions = [steps.index(href) for href in
                 ('#bot-info', '#persona', '#model-pref', '#master', '#superusers')]
    assert positions == sorted(positions)
    assert 'async function basicsGuideNext' in text
    assert 'async function basicsGuidePrev' in text
    assert 'async function loadOnboarding' in text
    assert 'await run(loadOnboarding)' in text
    assert "'/api/onboarding'" in text
    assert 'syncBasicsGuideStepFromHash' in text
    assert '完成并重启bot' in text
    assert 'await restartBotNow()' in text


def test_mobile_layout_hooks_remain_present():
    text = _html()
    styles = _styles(text)
    assert '@media(max-width:800px)' in styles
    assert re.search(r'id="navToggle"[^>]*aria-controls="appSidebar"', text)
    assert 'id="navOverlay"' in text
    assert 'id="appSidebar"' in text
    assert 'id="appearanceButtonMobile"' in text
    assert re.search(r'\.topline-theme\{display:inline-flex\}', styles)


def test_holy_layout_has_no_right_context_rail():
    text = _html()
    styles = _styles(text)
    assert 'contextRail' not in text
    assert 'context-rail' not in text
    assert 'railLinks' not in text
    assert 'ON THIS PAGE' not in text
    assert 'margin-right:304px' not in styles
    assert not re.search(r'html\[data-skin="holy"\][^{}]*\.context-rail', styles)

    workspace = re.search(r'html\[data-skin="holy"\] \.workspace\{([^}]*)\}', styles)
    assert workspace is not None
    assert 'margin-right:0' in workspace.group(1)
    assert 'max-width:1440px' in workspace.group(1)


def test_runtime_log_page_is_wired_to_the_whitelisted_api_and_filters():
    text = _html()
    assert '<h2>运行日志</h2>' in text
    assert 'href="#events"' in text
    assert 'id="logSource"' in text
    assert 'id="logTail"' in text
    assert 'id="logFilter"' in text
    assert 'id="logAutoScroll"' in text
    assert 'id="parserEventsPanel"' in text
    assert 'id="eventKind"' in text
    assert 'id="eventOnlyLimit"' not in text
    assert 'async function loadRuntimeLogs' in text
    assert '/api/logs' in text
    assert 'async function loadLogsPage' in text


def test_shader_primary_action_uses_readable_theme_colors():
    styles = _styles(_html())
    rules = re.findall(
        r'html\[data-skin="shader"\] #app \.btn-primary(?::hover)?\{([^}]*)\}',
        styles,
    )
    assert rules, 'shader must override the generic .btn-primary ink/paper colors'
    joined = ' '.join(rules)
    assert 'background:var(--accent)' in joined
    assert 'color:var(--on-accent)' in joined
    assert 'color:var(--paper)' not in joined


def test_host_overview_logs_and_sticker_tools_are_wired():
    text = _html()
    for element_id in (
        'hostCpuPercent', 'statusCores', 'statusSystem', 'statusDeps',
        'memeImportState', 'logPause', 'logCounts',
        'chatLog', 'chatInput', 'chatSend', 'chatModel',
    ):
        assert f'id="{element_id}"' in text, element_id
    assert 'id="modernControls"' in text
    assert 'class="host-overview"' in text
    assert 'async function importMemes' in text
    assert 'async function autoTagMemes' in text
    assert 'async function sendModelChat' in text
    assert "'/api/model/chat'" in text
    assert 'function startLogPolling' in text


def test_bilibili_credentials_import_and_qr_are_wired():
    text = _html()
    parser = _between(text, '<section id="parser"', '</section>')
    assert 'id="biliCookieInput"' in parser
    assert 'id="biliCookieImport"' in parser
    assert 'id="biliCookieFile"' in parser
    assert 'type="file"' in parser
    assert 'accept=".txt,text/plain"' in parser
    template = _between(text, '<template id="biliCredentialsTemplate"', '</template>')
    assert 'id="biliCookieFile"' in template
    assert 'type="file"' in template
    assert 'importBiliCookieFile(this)' in template
    assert 'id="biliQrImage"' in parser
    assert 'id="biliQrStart"' in parser
    assert 'id="biliCookieState"' in parser
    assert 'importBiliCookies' in text
    assert 'importBiliCookieFile' in text
    assert 'startBiliQr' in text
    assert 'pollBiliQr' in text
    assert '/api/parser/bilibili/cookies' in text
    assert '/api/parser/bilibili/qr' in text


def test_overview_p95_and_rate_kpis_are_wired():
    text = _html()
    for key in ('kpiP95', 'kpiRate'):
        assert f"$('{key}')" in text, f'{key} 没有 JS 赋值，会一直是空壳'
        assert f"$('{key}Small')" in text, f'{key} 的小字说明没有更新'
    assert 'statusMs(' in text


def test_parser_platform_tabs_and_xhs_login_are_wired():
    text = _html()
    parser = _between(text, '<section id="parser"', '</section>')
    assert 'id="parserTabs"' in parser
    assert 'role="tablist"' in parser
    assert 'id="parserGeneralPanel"' in parser
    assert 'id="parserPlatformPanel"' in parser
    assert 'id="parserSave"' in parser
    assert 'function parserSelectScope' in text
    assert 'function parserFocusField' in text
    assert 'id="xhsLoginStart"' in text
    assert 'id="xhsLoginQr"' in text
    assert '/api/parser/xhs-login/start' in text
    assert 'xhs_login_id' in text
    assert 'FileReader' in text
    assert 'readAsText' in text
    assert 'importPlatformCookieFile' in text
    assert '/api/parser/cookies' in text
    assert "file.type='file'" in text
    assert 'COOKIE_FILE_ACCEPT' in text
    assert 'parser-cookie-file' in text
