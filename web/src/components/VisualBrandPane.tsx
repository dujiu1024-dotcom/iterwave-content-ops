import { useEffect, useState } from "react";
import { api, VisualBrandProfile, VisualVersionHistory } from "../api";
import { useApp } from "../AppContext";

const STYLES: Array<{ value: NonNullable<VisualBrandProfile["style_key"]>; label: string; note: string }> = [
  { value: "knowledge_infographic", label: "知识信息图", note: "结构清晰，适合步骤、知识点和数据" },
  { value: "editorial_magazine", label: "杂志编辑", note: "编辑感排版，适合观点与深度内容" },
  { value: "business_poster", label: "商业海报", note: "强调标题与行动信息" },
  { value: "lifestyle", label: "生活方式", note: "轻量、舒展，适合生活方式内容" },
];

export default function VisualBrandPane() {
  const { showToast, withPending } = useApp();
  const [profile, setProfile] = useState<VisualBrandProfile>({ style_key: "knowledge_infographic", forbidden_styles: [] });
  const [contentId, setContentId] = useState("");
  const [versions, setVersions] = useState<VisualVersionHistory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revising, setRevising] = useState("");
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof api.visualUsage>> | null>(null);
  useEffect(() => {
    api.visualUsage().then(setUsage).catch(() => undefined);
    api.visualBrandProfile().then((result) => {
      setProfile({ style_key: "knowledge_infographic", forbidden_styles: [], ...result.profile });
    }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  const update = <K extends keyof VisualBrandProfile>(key: K, value: VisualBrandProfile[K]) =>
    setProfile((current) => ({ ...current, [key]: value }));
  const save = () => withPending("visual-brand-save", async () => {
    try {
      const result = await api.saveVisualBrandProfile(profile);
      setProfile(result.profile);
      showToast("视觉品牌规范已保存，仅作用于当前账号组", "success");
    } catch (e) { showToast(`保存失败：${e instanceof Error ? e.message : String(e)}`, "error"); }
  });
  const revisePage = async (versionId: number, pageIndex: number) => {
    const instruction = window.prompt("输入本页视觉调整要求", "改善本页构图、色彩协调和视觉层次");
    if (instruction === null) return;
    setRevising(`${versionId}:${pageIndex}`); setError("");
    try {
      await api.reviseVisualPage(contentId.trim(), versionId, pageIndex, instruction);
      showToast("单页已重新生成，新版本已建立，需人工审核", "success");
      await loadVersions();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setRevising(""); }
  };
  const loadVersions = async () => {
    if (!contentId.trim()) return;
    setLoading(true); setError("");
    try { setVersions((await api.visualVersions(contentId.trim())).versions); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <section className="panel">
        <div className="panel-head"><div><div className="panel-title">视觉风格与品牌规范</div><div className="panel-sub">配置按账号组隔离；新账号组使用默认值，不继承其他账号组。</div></div></div>
        {error && <div className="note" style={{ color: "var(--red)" }}>{error}</div>}
        <div className="form-grid">
          <label className="field-label">默认风格
            <select className="field" value={profile.style_key ?? "knowledge_infographic"} onChange={(e) => update("style_key", e.target.value as VisualBrandProfile["style_key"])}>
              {STYLES.map((style) => <option key={style.value} value={style.value}>{style.label}</option>)}
            </select>
          </label>
          <label className="field-label">品牌名称
            <input className="field" maxLength={120} value={profile.brand_name ?? ""} onChange={(e) => update("brand_name", e.target.value)} placeholder="可留空" />
          </label>
          <label className="field-label">主色（#RRGGBB）
            <input className="field" value={profile.primary_color ?? ""} onChange={(e) => update("primary_color", e.target.value)} placeholder="#263B55" />
          </label>
          <label className="field-label">辅助色（#RRGGBB）
            <input className="field" value={profile.secondary_color ?? ""} onChange={(e) => update("secondary_color", e.target.value)} placeholder="#D9A441" />
          </label>
          <label className="field-label">背景色（#RRGGBB）
            <input className="field" value={profile.background_color ?? ""} onChange={(e) => update("background_color", e.target.value)} placeholder="#F3F5F7" />
          </label>
          <label className="field-label">正文色（#RRGGBB）
            <input className="field" value={profile.text_color ?? ""} onChange={(e) => update("text_color", e.target.value)} placeholder="#17212B" />
          </label>
          <label className="field-label">信息组件样式
            <select className="field" value={profile.component_style ?? "card"} onChange={(e) => update("component_style", e.target.value as VisualBrandProfile["component_style"])}>
              <option value="card">卡片式</option>
              <option value="minimal">极简式</option>
              <option value="editorial">编辑式</option>
            </select>
          </label>
          <label className="field-label">字体名称
            <select className="field" value={profile.font_family ?? ""} onChange={(e) => update("font_family", e.target.value || undefined)}>
              <option value="">系统默认字体（Noto Sans CJK SC）</option>
              <option value="Noto Sans CJK SC">Noto Sans CJK SC</option>
              <option value="Source Han Sans SC">Source Han Sans SC</option>
              <option value="Microsoft YaHei">Microsoft YaHei</option>
              <option value="Arial">Arial</option>
            </select>
          </label>
          <label className="field-label">页脚落款
            <input className="field" maxLength={120} value={profile.footer ?? ""} onChange={(e) => update("footer", e.target.value)} placeholder="可留空" />
          </label>
        </div>
        <label className="field-label" style={{ display: "block", marginTop: 12 }}>禁用风格（每行一项）
          <textarea className="field" rows={3} value={(profile.forbidden_styles ?? []).join("\n")} onChange={(e) => update("forbidden_styles", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 20))} />
        </label>
        <div className="toolbar" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn primary" onClick={save}>保存品牌规范</button>
        </div>
        <div className="note" style={{ marginTop: 8 }}>品牌风格、色彩与字体将应用于后续生成；历史图片保持不变。单页重做需要启用 AI 视觉生图，并且该版本必须保留原始背景素材。</div>
      </section>
      <section className="panel">
        <div className="panel-head"><div><div className="panel-title">视觉生成用量</div><div className="panel-sub">统计当前账号组最近 500 个轮次报告中的生图记录。</div></div></div>
        {usage ? (
          <>
            <div className="form-grid">
              <div><div className="panel-sub">生图次数</div><strong>{usage.totals.generation_count}</strong></div>
              <div><div className="panel-sub">累计素材体积</div><strong>{(usage.totals.bytes / 1024 / 1024).toFixed(2)} MB</strong></div>
              <div><div className="panel-sub">累计耗时</div><strong>{(usage.totals.elapsed_ms / 1000).toFixed(1)} 秒</strong></div>
              <div><div className="panel-sub">生产记录</div><strong>{usage.totals.items}</strong></div>
            </div>
            <div className="panel-sub" style={{ marginTop: 10 }}>
              {usage.by_platform.map((item) => `${item.platform}: ${item.generation_count} 次`).join(" · ") || "暂无用量"}
            </div>
          </>
        ) : <div className="note">用量数据暂不可用。</div>}
      </section>
      <section className="panel">
        <div className="panel-head"><div><div className="panel-title">视觉版本历史</div><div className="panel-sub">输入内容 ID，查看该内容的视觉版本及历史页面。</div></div></div>
        <div className="toolbar">
          <input className="field" value={contentId} onChange={(e) => setContentId(e.target.value)} placeholder="内容 ID" />
          <button className="btn sm" onClick={loadVersions} disabled={loading || !contentId.trim()}>{loading ? "加载中…" : "查询版本"}</button>
        </div>
        {versions.map((version) => (
          <div key={version.id} className="panel" style={{ marginTop: 10 }}>
            <div className="toolbar" style={{ justifyContent: "space-between" }}>
              <strong>视觉版本 v{version.version}</strong>
              <span className="tag blue">{version.status}</span>
            </div>
            <div className="panel-sub">{version.style_key} · {version.renderer} · {version.created_at}</div>
            <div className="toolbar" style={{ flexWrap: "wrap", marginTop: 8 }}>
              {(version.design.pages ?? []).map((page) => page.image_url && (
                <span key={page.page_index} className="toolbar" style={{ gap: 6 }}>
                  <a href={page.image_url} target="_blank" rel="noreferrer" className="btn sm">查看第 {page.page_index + 1} 页</a>
                  <button className="btn sm" disabled={!!revising || !page.can_revise} title={page.can_revise ? "重做此页" : "该历史版本没有可编辑的原始背景"} onClick={() => revisePage(version.id, page.page_index)}>
                    {revising === `${version.id}:${page.page_index}` ? "重做中…" : "重做此页"}
                  </button>
                </span>
              ))}
            </div>
          </div>
        ))}
        {!loading && contentId && versions.length === 0 && <div className="note">没有找到视觉版本记录。</div>}
      </section>
    </div>
  );
}
