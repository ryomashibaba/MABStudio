"""One-click offline/local readiness diagnostics; NO model inference or world access.

Designed to explain Windows setup blockers without replacing genuine tests. In
particular, listing a model is not proof it can perform a successful inference.
"""
from __future__ import annotations
from app.core import StudioError, compatibility_check, llm_models, node_binary


def check_local_setup(manager, provider: str) -> dict:
    """Safe observations only; never installs or launches any service."""
    if provider not in ('Ollama', 'LM Studio'):
        raise StudioError('診断対象のローカルAIプロバイダーが不正です')
    report = {'schema': 'mab-studio-local-readiness-v1',
              'provider': provider, 'node': 'NOT_FOUND',
              'engine': 'NOT_INSTALLED', 'modelServer': 'NOT_CONNECTED',
              'models': [], 'inference': 'NOT_TESTED',
              'educationWebSocket': 'NOT_TESTED', 'worldContacts': 0, 'worldWrites': 0}
    try:
        node_binary()
        report['node'] = 'AVAILABLE'
    except (StudioError, OSError) as exc:
        report['nodeNote'] = str(exc)[:200]
    engine = manager.active_path
    if engine is None:
        report['engineNote'] = 'MABエンジン未導入、またはSHA改変・互換性確認が必要です。'
    elif report['node'] == 'AVAILABLE':
        try:
            compatibility_check(engine)
            report['engine'] = 'VERIFIED_B143'
        except (StudioError, OSError, ValueError) as exc:
            report['engine'] = 'COMPATIBILITY_FAILED'
            report['engineNote'] = str(exc)[:240]
    else:
        report['engine'] = 'BLOCKED_NO_NODE'
    try:
        names = llm_models(provider)
        if len(names) > 300:
            raise StudioError('モデル一覧が多すぎます')
        report['models'] = names
        report['modelServer'] = 'CONNECTED' if names else 'CONNECTED_NO_MODELS'
    except (StudioError, OSError, ValueError, TypeError, KeyError) as exc:
        report['modelNote'] = str(exc)[:240]
    report['designReadyToTry'] = (report['engine'] == 'VERIFIED_B143'
                                  and report['modelServer'] == 'CONNECTED')
    return report


def readiness_text(report: dict) -> str:
    """Plain, accurate advice. Connection success != real model-generation proof."""
    engine = report['engine']
    service = report['modelServer']
    lines = [f'MAB SOURCEエンジン: {engine}',
             f'Node.js: {report["node"]}',
             f'{report["provider"]}: {service}',
             f'モデル: {", ".join(report["models"][:4]) if report["models"] else "未取得"}']
    if len(report['models']) > 4:
        lines.append(f'ほか {len(report["models"]) - 4} モデル')
    if report.get('engineNote'):
        lines.append('MAB: ' + report['engineNote'])
    if report.get('modelNote'):
        lines.append('AI: ' + report['modelNote'])
    if report['designReadyToTry']:
        lines.append('次は「AIで設計して3Dプレビュー」で真正推論を試せます。')
    else:
        lines.append('MABを固定SHAで導入し、ローカルAIサーバーを起動してモデルをロードしてください。')
    lines.append('※モデル一覧取得は推論の成功を意味しません。Minecraft接続・施工は未検証です。')
    return '\n'.join(lines)
