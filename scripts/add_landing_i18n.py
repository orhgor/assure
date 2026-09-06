#!/usr/bin/env python3
"""Insert landing.* keys after sandbox.placeholder in each locale dict."""

from __future__ import annotations

import re
from pathlib import Path

I18N = Path(__file__).resolve().parents[1] / "prompt_matrix" / "i18n.py"

# Keys already in EN via manual edit; patch other locales + update onboarding.step3
LOCALES = {
    "ES": "es",
    "ZH": "zh",
    "FR": "fr",
    "DE": "de",
    "JA": "ja",
    "TR": "tr",
}

TRANSLATIONS = {
    "es": {
        "onboarding.step3": "Observa el estado de verificación aquí: Inactivo, Procesando, Verificado o Problemas detectados.",
        "landing.nav.docs": "Docs",
        "landing.nav.architecture": "Arquitectura",
        "landing.nav.sandbox": "Sandbox",
        "landing.nav.launch": "Abrir espacio de trabajo →",
        "landing.nav.menu": "Menú",
        "landing.hero.sub": "Ingesta escaneos, CIM o datos desordenados. Bloquea variables con pruebas matemáticas. Edita nodos modulares con aislamiento por contexto. Deja de adivinar — compila.",
        "landing.hero.cta.paste": "Prueba gratuita de pegado",
        "landing.hero.cta.architecture": "Leer la arquitectura →",
        "landing.hero.caption": "De texto plano a verdad compilada — en segundos.",
        "landing.persona.strip_label": "Para profesionales que no pueden permitirse equivocarse:",
        "landing.persona.bankers": "Banca de inversión",
        "landing.persona.researchers": "Investigadores",
        "landing.persona.legal": "Asesoría legal",
        "landing.persona.more": "y más →",
        "landing.trust.self_hosted": "Autoalojado y privado",
        "landing.trust.z3": "Verificado simbólicamente con Z3",
        "landing.trust.jdf": "Arquitectura JDF AST",
        "landing.trust.redhat": "Auditado adversarialmente Red-Hat",
        "landing.sandbox.title": "Prueba de pegado sin riesgo",
        "landing.sandbox.sub": "Pega un párrafo con métricas financieras o afirmaciones. Observa cómo el motor aísla nodos, bloquea variables y señala brechas lógicas.",
        "landing.sandbox.run": "Ejecutar auditoría determinista",
        "landing.sandbox.advanced": "Modelos avanzados",
        "landing.sandbox.advanced_hint": "Se aplica al enviar resultados al espacio de trabajo — no cambia la auditoría del sandbox.",
        "landing.sandbox.error.empty": "Introduce texto para probar.",
        "landing.sandbox.error.failed": "Falló la verificación del sandbox.",
        "landing.sandbox.error.network": "Error de red durante la auditoría.",
        "landing.competitor.title": "Comparativa con competidores",
        "landing.competitor.quote": "Spellbook ayuda a escribir más rápido. Harvey ayuda a encontrar contexto. Assure garantiza que el documento final sea matemáticamente verdadero. ¿Cuál revisa el auditor?",
        "landing.personas.title": "Diseñado para trabajo de alto riesgo",
        "landing.cta.title": "La era de adivinar probabilísticamente terminó.",
        "landing.cta.sub": "Ingeniería documental respaldada por certeza matemática.",
        "landing.cta.btn": "Abrir espacio Assure",
        "landing.social_proof": "Usado por analistas, investigadores e ingenieros en instituciones de alta consecuencia.",
        "landing.footer.copy": "Assure — El Compilador Intelectual. Compila intención. Verifica lógica. Entrega verdad.",
        "settings.show_citations": "Incluir sección de referencias en exportación DOCX",
    },
    "zh": {
        "onboarding.step3": "在此查看验证状态：空闲、处理中、已验证或发现问题。",
        "landing.nav.docs": "文档",
        "landing.nav.architecture": "架构",
        "landing.nav.sandbox": "沙盒",
        "landing.nav.launch": "进入工作台 →",
        "landing.nav.menu": "菜单",
        "landing.hero.sub": "导入扫描件、CIM 或数据集。用数学证明锁定变量。在上下文隔离下编辑模块化节点。停止猜测——开始编译。",
        "landing.hero.cta.paste": "免费粘贴测试",
        "landing.hero.cta.architecture": "阅读架构 →",
        "landing.hero.caption": "从平文本到编译真相——数秒完成。",
        "landing.persona.strip_label": "为不能出错的专业人士而设：",
        "landing.persona.bankers": "投资银行家",
        "landing.persona.researchers": "研究人员",
        "landing.persona.legal": "法律顾问",
        "landing.persona.more": "更多 →",
        "landing.trust.self_hosted": "自托管且私密",
        "landing.trust.z3": "Z3 符号验证",
        "landing.trust.jdf": "JDF AST 架构",
        "landing.trust.redhat": "Red-Hat 对抗审计",
        "landing.sandbox.title": "零风险粘贴测试",
        "landing.sandbox.sub": "粘贴包含财务指标或事实声明的段落。观察引擎如何隔离节点、锁定变量并标记逻辑漏洞。",
        "landing.sandbox.run": "运行确定性审计",
        "landing.sandbox.advanced": "高级模型",
        "landing.sandbox.advanced_hint": "仅在发送到工作台时生效——不会改变沙盒审计。",
        "landing.sandbox.error.empty": "请输入要测试的文本。",
        "landing.sandbox.error.failed": "沙盒验证失败。",
        "landing.sandbox.error.network": "审计期间网络错误。",
        "landing.competitor.title": "竞品对照",
        "landing.competitor.quote": "Spellbook 让你写得更快。Harvey 帮你找上下文。Assure 确保最终文档在数学上为真。审计师查哪一个？",
        "landing.personas.title": "为高风险工作而设计",
        "landing.cta.title": "概率猜测的时代结束了。",
        "landing.cta.sub": "以数学确定性为支撑的文档工程。",
        "landing.cta.btn": "启动 Assure 工作台",
        "landing.social_proof": "被高风险机构的分析师、研究人员和工程师使用。",
        "landing.footer.copy": "Assure — 智能编译器。编译意图。验证逻辑。交付真相。",
        "settings.show_citations": "在 DOCX 导出中包含参考文献部分",
    },
    "fr": {
        "onboarding.step3": "Suivez l'état de vérification ici — Inactif, En cours, Vérifié ou Problèmes détectés.",
        "landing.nav.docs": "Docs",
        "landing.nav.architecture": "Architecture",
        "landing.nav.sandbox": "Sandbox",
        "landing.nav.launch": "Ouvrir l'espace de travail →",
        "landing.nav.menu": "Menu",
        "landing.hero.sub": "Importez des scans, CIM ou jeux de données. Verrouillez les variables par preuves mathématiques. Éditez des nœuds modulaires avec isolation contextuelle. Arrêtez de deviner — compilez.",
        "landing.hero.cta.paste": "Essai gratuit par collage",
        "landing.hero.cta.architecture": "Lire l'architecture →",
        "landing.hero.caption": "Du texte plat à la vérité compilée — en quelques secondes.",
        "landing.persona.strip_label": "Pour les professionnels qui ne peuvent pas se tromper :",
        "landing.persona.bankers": "Banquiers d'investissement",
        "landing.persona.researchers": "Chercheurs",
        "landing.persona.legal": "Conseil juridique",
        "landing.persona.more": "et plus →",
        "landing.trust.self_hosted": "Auto-hébergé et privé",
        "landing.trust.z3": "Vérifié symboliquement par Z3",
        "landing.trust.jdf": "Architecture JDF AST",
        "landing.trust.redhat": "Audité adversarial Red-Hat",
        "landing.sandbox.title": "Test de collage sans risque",
        "landing.sandbox.sub": "Collez un paragraphe avec métriques financières ou affirmations. Observez l'isolation des nœuds, le verrouillage et les écarts logiques.",
        "landing.sandbox.run": "Lancer l'audit déterministe",
        "landing.sandbox.advanced": "Modèles avancés",
        "landing.sandbox.advanced_hint": "S'applique à l'envoi vers l'espace de travail — ne change pas l'audit sandbox.",
        "landing.sandbox.error.empty": "Saisissez du texte à tester.",
        "landing.sandbox.error.failed": "Échec de la vérification sandbox.",
        "landing.sandbox.error.network": "Erreur réseau pendant l'audit.",
        "landing.competitor.title": "Réalité concurrentielle",
        "landing.competitor.quote": "Spellbook accélère l'écriture. Harvey trouve le contexte. Assure garantit que le document final est mathématiquement vrai. Lequel l'auditeur vérifie ?",
        "landing.personas.title": "Conçu pour le travail à enjeux élevés",
        "landing.cta.title": "L'ère des suppositions probabilistes est révolue.",
        "landing.cta.sub": "Ingénierie documentaire fondée sur la certitude mathématique.",
        "landing.cta.btn": "Lancer l'espace Assure",
        "landing.social_proof": "Utilisé par analystes, chercheurs et ingénieurs dans des institutions à haute conséquence.",
        "landing.footer.copy": "Assure — Le Compilateur Intellectuel. Compilez l'intention. Vérifiez la logique. Livrez la vérité.",
        "settings.show_citations": "Inclure la section Références dans l'export DOCX",
    },
    "de": {
        "onboarding.step3": "Verifizierungsstatus hier beobachten — Leerlauf, Verarbeitung, Verifiziert oder Probleme.",
        "landing.nav.docs": "Docs",
        "landing.nav.architecture": "Architektur",
        "landing.nav.sandbox": "Sandbox",
        "landing.nav.launch": "Arbeitsbereich öffnen →",
        "landing.nav.menu": "Menü",
        "landing.hero.sub": "Scans, CIMs oder Datensätze importieren. Variablen mit mathematischen Beweisen sperren. Modulare Knoten mit Kontext-Isolation bearbeiten. Schluss mit Raten — kompilieren.",
        "landing.hero.cta.paste": "Kostenloser Paste-Test",
        "landing.hero.cta.architecture": "Architektur lesen →",
        "landing.hero.caption": "Von Flachtext zu kompilierter Wahrheit — in Sekunden.",
        "landing.persona.strip_label": "Für Fachleute, die sich keinen Fehler leisten können:",
        "landing.persona.bankers": "Investmentbanker",
        "landing.persona.researchers": "Forscher",
        "landing.persona.legal": "Rechtsberater",
        "landing.persona.more": "und mehr →",
        "landing.trust.self_hosted": "Self-hosted & privat",
        "landing.trust.z3": "Symbolisch mit Z3 verifiziert",
        "landing.trust.jdf": "JDF-AST-Architektur",
        "landing.trust.redhat": "Red-Hat-Gegenprüfung",
        "landing.sandbox.title": "Risikofreier Paste-Test",
        "landing.sandbox.sub": "Fügen Sie einen Absatz mit Finanzkennzahlen oder Behauptungen ein. Beobachten Sie Knoten, Sperren und Logiklücken.",
        "landing.sandbox.run": "Deterministisches Audit starten",
        "landing.sandbox.advanced": "Erweiterte Modelle",
        "landing.sandbox.advanced_hint": "Gilt beim Senden an den Arbeitsbereich — ändert nicht das Sandbox-Audit.",
        "landing.sandbox.error.empty": "Bitte Text zum Testen eingeben.",
        "landing.sandbox.error.failed": "Sandbox-Verifizierung fehlgeschlagen.",
        "landing.sandbox.error.network": "Netzwerkfehler während des Audits.",
        "landing.competitor.title": "Wettbewerbsrealität",
        "landing.competitor.quote": "Spellbook schreibt schneller. Harvey findet Kontext. Assure stellt sicher, dass das Enddokument mathematisch wahr ist. Was prüft der Auditor?",
        "landing.personas.title": "Für hochriskante Arbeit",
        "landing.cta.title": "Das Zeitalter probabilistischen Raten ist vorbei.",
        "landing.cta.sub": "Dokumentenengineering mit mathematischer Sicherheit.",
        "landing.cta.btn": "Assure-Arbeitsbereich starten",
        "landing.social_proof": "Genutzt von Analysten, Forschern und Ingenieuren in hochriskanten Institutionen.",
        "landing.footer.copy": "Assure — Der Intellektuelle Compiler. Absicht kompilieren. Logik prüfen. Wahrheit liefern.",
        "settings.show_citations": "Referenzabschnitt im DOCX-Export einschließen",
    },
    "ja": {
        "onboarding.step3": "ここで検証ステータスを確認——待機、処理中、検証済み、問題あり。",
        "landing.nav.docs": "ドキュメント",
        "landing.nav.architecture": "アーキテクチャ",
        "landing.nav.sandbox": "サンドボックス",
        "landing.nav.launch": "ワークスペースを開く →",
        "landing.nav.menu": "メニュー",
        "landing.hero.sub": "スキャン、CIM、データセットを取り込み。数学的証明で変数をロック。コンテキスト分離でモジュールノードを編集。推測をやめてコンパイル。",
        "landing.hero.cta.paste": "無料ペーストテスト",
        "landing.hero.cta.architecture": "アーキテクチャを読む →",
        "landing.hero.caption": "フラットテキストからコンパイルされた真実へ——数秒で。",
        "landing.persona.strip_label": "間違えられないプロフェッショナルのために：",
        "landing.persona.bankers": "投資銀行家",
        "landing.persona.researchers": "研究者",
        "landing.persona.legal": "法務顧問",
        "landing.persona.more": "その他 →",
        "landing.trust.self_hosted": "セルフホスト＆プライベート",
        "landing.trust.z3": "Z3 記号検証",
        "landing.trust.jdf": "JDF AST アーキテクチャ",
        "landing.trust.redhat": "Red-Hat 敵対監査",
        "landing.sandbox.title": "ゼロリスク・ペーストテスト",
        "landing.sandbox.sub": "財務指標や事実を含む段落を貼り付け。ノード分離、変数ロック、論理ギャップを即座に確認。",
        "landing.sandbox.run": "決定論的監査を実行",
        "landing.sandbox.advanced": "高度なモデル",
        "landing.sandbox.advanced_hint": "ワークベンチへ送るときに適用——サンドボックス監査は変わりません。",
        "landing.sandbox.error.empty": "テストするテキストを入力してください。",
        "landing.sandbox.error.failed": "サンドボックス検証に失敗しました。",
        "landing.sandbox.error.network": "監査中のネットワークエラー。",
        "landing.competitor.title": "競合との比較",
        "landing.competitor.quote": "Spellbook は速く書く。Harvey は文脈を見つける。Assure は最終文書が数学的に真実であることを保証する。監査人が見るのはどれ？",
        "landing.personas.title": "ハイステークス向けに設計",
        "landing.cta.title": "確率的推測の時代は終わった。",
        "landing.cta.sub": "数学的確実性に基づくドキュメントエンジニアリング。",
        "landing.cta.btn": "Assure ワークスペースを起動",
        "landing.social_proof": "高リスク機関の分析官、研究者、エンジニアが利用。",
        "landing.footer.copy": "Assure — インテレクチュアル・コンパイラー。意図をコンパイル。論理を検証。真実を届ける。",
        "settings.show_citations": "DOCX エクスポートに参考文献セクションを含める",
    },
    "tr": {
        "onboarding.step3": "Doğrulama durumunu buradan izleyin — Boşta, İşleniyor, Doğrulandı veya Sorun bulundu.",
        "landing.nav.docs": "Belgeler",
        "landing.nav.architecture": "Mimari",
        "landing.nav.sandbox": "Sandbox",
        "landing.nav.launch": "Çalışma alanını aç →",
        "landing.nav.menu": "Menü",
        "landing.hero.sub": "Dağınık taramalar, CIM veya veri setleri yükleyin. Değişkenleri matematiksel kanıtlarla kilitleyin. Bağlam kilitlemeli modüler düğümleri düzenleyin. Tahmin etmeyi bırakın — derleyin.",
        "landing.hero.cta.paste": "Ücretsiz yapıştırma testi",
        "landing.hero.cta.architecture": "Mimariyi oku →",
        "landing.hero.caption": "Düz metinden derlenmiş gerçeğe — saniyeler içinde.",
        "landing.persona.strip_label": "Hata lüksü olmayan profesyoneller için:",
        "landing.persona.bankers": "Yatırım bankacıları",
        "landing.persona.researchers": "Araştırmacılar",
        "landing.persona.legal": "Hukuk danışmanları",
        "landing.persona.more": "ve daha fazlası →",
        "landing.trust.self_hosted": "Self-hosted ve özel",
        "landing.trust.z3": "Z3 ile sembolik doğrulama",
        "landing.trust.jdf": "JDF AST mimarisi",
        "landing.trust.redhat": "Red-Hat karşıt denetim",
        "landing.sandbox.title": "Sıfır riskli yapıştırma testi",
        "landing.sandbox.sub": "Finansal metrik veya iddia içeren bir paragraf yapıştırın. Motor düğümleri ayırır, değişkenleri kilitler ve mantık boşluklarını işaretler.",
        "landing.sandbox.run": "Denetimi çalıştır",
        "landing.sandbox.advanced": "Gelişmiş modeller",
        "landing.sandbox.advanced_hint": "Workbench'e gönderirken geçerlidir — sandbox denetimini değiştirmez.",
        "landing.sandbox.error.empty": "Test için metin girin.",
        "landing.sandbox.error.failed": "Sandbox doğrulaması başarısız.",
        "landing.sandbox.error.network": "Denetim sırasında ağ hatası.",
        "landing.competitor.title": "Rakip gerçekliği",
        "landing.competitor.quote": "Spellbook daha hızlı yazdırır. Harvey bağlam bulur. Assure nihai belgenin matematiksel olarak doğru olmasını sağlar. Denetçi hangisine bakar?",
        "landing.personas.title": "Yüksek riskli iş için tasarlandı",
        "landing.cta.title": "Olasılıksal tahmin çağı bitti.",
        "landing.cta.sub": "Matematiksel kesinlikle desteklenen belge mühendisliği.",
        "landing.cta.btn": "Assure çalışma alanını aç",
        "landing.social_proof": "Yüksek sonuçlu kurumlarda analist, araştırmacı ve mühendisler tarafından kullanılır.",
        "landing.footer.copy": "Assure — Zihinsel Derleyici — Bilgiyi derleyin. Mantığı doğrulayın. Gerçeği teslim edin.",
        "settings.show_citations": "DOCX dışa aktarmada Kaynaklar bölümünü ekle",
    },
}


def section_bounds(text: str, dict_name: str) -> tuple[int, int]:
    start = text.index(f"{dict_name} = {{")
    rest = text[start + 1 :]
    nxt = re.search(r"\n[A-Z_]+ = \{", rest)
    end = start + 1 + nxt.start() if nxt else len(text)
    return start, end


def main() -> None:
    text = I18N.read_text(encoding="utf-8")
    for dict_name, loc in LOCALES.items():
        entries = TRANSLATIONS[loc]
        s, e = section_bounds(text, dict_name)
        block = text[s:e]
        for key, val in entries.items():
            esc = val.replace("\\", "\\\\").replace('"', '\\"')
            line = f'    "{key}": "{esc}",\n'
            pat = rf'    "{re.escape(key)}": "[^"]*",\n'
            if re.search(pat, block):
                block = re.sub(pat, line, block, count=1)
            elif key.startswith("landing.") or key == "settings.show_citations":
                anchor = '    "stream.reconnect":'
                if anchor in block:
                    block = block.replace(anchor, line + anchor, 1)
                else:
                    anchor2 = '    "sandbox.placeholder":'
                    idx = block.find(anchor2)
                    if idx == -1:
                        raise SystemExit(f"No anchor in {dict_name}")
                    line_end = block.find("\n", idx) + 1
                    block = block[:line_end] + line + block[line_end:]
        text = text[:s] + block + text[e:]
    I18N.write_text(text, encoding="utf-8")
    print("Patched ES ZH FR DE JA TR")


if __name__ == "__main__":
    main()
