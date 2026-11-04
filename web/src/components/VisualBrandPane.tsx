import { useEffect, useState } from "react";
import { api, VisualBrandProfile, VisualCatalog, VisualVersionHistory } from "../api";
import { useApp } from "../AppContext";
import { displayStatusLabel, displayPlatformLabel } from "../displayLabels";

// 风格、字体、组件样式的可选项全部来自 GET /api/visual/catalog（后端 visual_catalog.json 单一来源）。
export default function VisualBrandPane() {
  const { showToast, withPending } = useApp();
  const [catalog, setCatalog] = useState<VisualCatalog | null>(null);
  const [profile, setProfile] = useState<VisualBrandProfile>({ forbidden_styles: [] });
  const [contentId, setContentId] = useState("");
  const [versions, setVersions] = useState<VisualVersionHistory[]>([]);
  const [regenerationHistory, setRegenerationHistory] = useState<Array<Record<string, unknown>>>([]);
  const [approvingVersion, setApprovingVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revising, setRevising] = useState("");
  const [usage, setUsage] = useState<Awaited<ReturnType<typeof api.visualUsage>> | null>(null);
  useEffect(() => {
    api.visualUsage().then(setUsage).catch(() => undefined);
    api.visualCatalog().then(setCatalog).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    api.visualBrandProfile().then((result) => {
      setProfile({ forbidden_styles: [], ...result.profile });
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
    const instruction = window.prompt("请填写本张图片的修改理由（必填）");
    if (instruction === null) return;
    if (!instruction.trim()) { setError("必须填写单张图片修改理由"); return; }
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
    try {
      const [versionResult, regenerationResult] = await Promise.all([
        api.visualVersions(contentId.trim()), api.visualRegenerationHistory(contentId.trim()),
      ]);
      setVersions(versionResult.versions);
      setRegenerationHistory(regenerationResult.items);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <section className="panel">
        <div className="panel-head"><div><div className="panel-title">视觉风格与品牌规范</div><div className="panel-sub">配置按账号组隔离；新账号组使用默认值，不继承其他账号组。</div></div></div>
        {error && <div className="note" style={{ color: "var(--red)" }}>{error}</div>}
        {!catalog ? <div className="note">正在加载可选风格与字体…</div> : (() => {
          const styleKey = profile.style_key || catalog.default_style;
          const style = catalog.styles.find((item) => item.key === styleKey) ?? catalog.styles[0];
          const forbidden = profile.forbidden_styles ?? [];
          const colorField = (key: "primary_color" | "secondary_color" | "background_color" | "text_color",
            label: string, token: "accent" | "muted" | "bg" | "fg") => (
            <label className="field-label">{label}
              <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span aria-hidden style={{ width: 22, height: 22, borderRadius: 6, flex: "0 0 auto",
                  border: "1px solid var(--line, #ddd)", background: profile[key] || style.palette[token] }} />
                <input className="field" value={profile[key] ?? ""} onChange={(e) => update(key, e.target.value)}
                  placeholder={`留空使用风格默认 ${style.palette[token]}`} />
              </span>
            </label>
          );
          return (
            <>
              <div className="form-grid">
                <label className="field-label">默认风格
                  <select className="field" value={styleKey} onChange={(e) => update("style_key", e.target.value)}>
                    {catalog.styles.map((item) => (
                      <option key={item.key} value={item.key} disabled={forbidden.includes(item.key)}>{item.label}</option>
                    ))}
                  </select>
                  <span className="panel-sub">{style.note}</span>
                </label>
                <label className="field-label">品牌名称
                  <input className="field" maxLength={120} value={profile.brand_name ?? ""} onChange={(e) => update("brand_name", e.target.value)} placeholder="可留空，填写后显示在页脚" />
                </label>
                {colorField("primary_color", "主色（#RRGGBB）", "accent")}
                {colorField("secondary_color", "辅助色（#RRGGBB）", "muted")}
                {colorField("background_color", "背景色（#RRGGBB）", "bg")}
                {colorField("text_color", "正文色（#RRGGBB）", "fg")}
                <label className="field-label">信息组件样式
                  <select className="field" value={profile.component_style || catalog.default_component_style} onChange={(e) => update("component_style", e.target.value)}>
                    {catalog.component_styles.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                  </select>
                </label>
                <label className="field-label">字体
                  <select className="field" value={profile.font_family || catalog.default_font} onChange={(e) => update("font_family", e.target.value)}>
                    {catalog.fonts.map((font) => (
                      <option key={font.family} value={font.family} disabled={!font.installed}>
                        {font.label}{font.installed ? "" : "（本机未安装，不可选）"}
                      </option>
                    ))}
                  </select>
                  <span className="panel-sub">缺少字体时生成会直接报错，不会静默替换字体。</span>
                </label>
                <label className="field-label">页脚落款
                  <input className="field" maxLength={120} value={profile.footer ?? ""} onChange={(e) => update("footer", e.target.value)} placeholder="可留空" />
                </label>
              </div>
              <div className="field-label" style={{ display: "block", marginTop: 12 }}>禁用风格
                <div className="toolbar" style={{ flexWrap: "wrap", gap: 12, marginTop: 6 }}>
                  {catalog.styles.map((item) => (
                    <label key={item.key} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                      <input type="checkbox" checked={forbidden.includes(item.key)} disabled={item.key === styleKey}
                        onChange={(e) => update("forbidden_styles", e.target.checked
                          ? [...forbidden, item.key] : forbidden.filter((key) => key !== item.key))} />
                      {item.label}
                    </label>
                  ))}
                </div>
              </div>
            </>
          );
        })()}
        <div className="toolbar" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn primary" onClick={save}>保存品牌规范</button>
        </div>
        <div className="note" style={{ marginTop: 8 }}>品牌风格、色彩与字体将应用于后续生成；历史图片保持不变。单页重做需要在「图片素材库设置」中启用 AI 配图，并且该页保留了原始 AI 底图。</div>
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
              {usage.by_platform.map((item) => `${displayPlatformLabel(item.platform)}：${item.generation_count} 次`).join(" · ") || "暂无用量"}
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
              <span className="tag blue">{displayStatusLabel(version.status)}</span>
            </div>
            <div className="panel-sub">{version.style_key} · {version.renderer} · {version.created_at}{version.approved_at ? ` · 人工采用于 ${version.approved_at}` : ""}</div>
            {version.status === "review_required" && <div className="note" style={{ marginTop: 6 }}>生成结果待人工审核；当前使用的原图保持不变。审核通过并采用后才会替换。</div>}
            {(version.status === "review_required" || version.status === "rendered") && (
              <div className="toolbar" style={{ marginTop: 8 }}>
              <button className="btn primary sm" style={{ marginTop: 8 }} disabled={approvingVersion !== null || !contentId.trim()}
                onClick={async () => {
                  if (!window.confirm("确认采用此视觉版本？采用后才会替换当前内容图片；原视觉版本仍保留在历史中。")) return;
                  setApprovingVersion(version.id);
                  try {
                    await api.approveVisualVersion(contentId.trim(), version.id);
                    await loadVersions();
                  } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                  finally { setApprovingVersion(null); }
                }}>
                {approvingVersion === version.id ? "采用中…" : "人工审核通过并采用此版本"}
              </button>
              <button className="btn sm" disabled={approvingVersion !== null} onClick={async () => {
                const reason = window.prompt("填写拒绝原因，说明风格或内容问题：");
                if (!reason?.trim()) return;
                setApprovingVersion(version.id);
                try {
                  await api.rejectVisualVersion(contentId.trim(), version.id, reason.trim());
                  await loadVersions();
                } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                finally { setApprovingVersion(null); }
              }}>拒绝此版本</button>
              </div>
            )}
            <div className="toolbar" style={{ flexWrap: "wrap", marginTop: 8 }}>
              {(version.design.pages ?? []).map((page) => page.image_url && (
                <span key={page.page_index} className="toolbar" style={{ gap: 6 }}>
                  <a href={page.image_url} target="_blank" rel="noreferrer" className="btn sm">查看第 {page.page_index + 1} 页</a>
                  <button className="btn sm" disabled={!!revising || !page.can_revise || version.status !== "approved"} title={version.status !== "approved" ? "单页重做必须基于当前已采用版本" : page.can_revise ? "重做此页" : "该版本没有可编辑的原始背景"} onClick={() => revisePage(version.id, page.page_index)}>
                    {revising === `${version.id}:${page.page_index}` ? "重做中…" : "重做此页"}
                  </button>
                </span>
              ))}
            </div>
          </div>
        ))}
        {!loading && contentId && versions.length === 0 && <div className="note">没有找到视觉版本记录。</div>}
        {regenerationHistory.length > 0 && <>
          <div className="panel-title" style={{ marginTop: 16 }}>图片重新生成记录</div>
          {regenerationHistory.map((item) => (
            <div key={String(item.request_id)} className="panel" style={{ marginTop: 8, padding: 10 }}>
              <div className="toolbar" style={{ justifyContent: "space-between" }}>
                <strong>{item.scope === "full" ? "整套重新生成" : `第 ${Number(item.page_index ?? 0) + 1} 页重新生成`}</strong>
                <span className="tag">{displayStatusLabel(String(item.status || ""))}</span>
              </div>
              <div className="panel-sub">理由：{String(item.original_reason || "")}</div>
              <div className="panel-sub">源版本 v{String(item.source_version_id ?? "—")} → 新版本 v{String(item.output_version_id ?? "未生成")} · {String(item.created_at || "")}</div>
              <details style={{ marginTop: 6 }}>
                <summary className="panel-sub" style={{ cursor: "pointer" }}>查看最终提示词与视觉基准</summary>
                <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 11, maxHeight: 260, overflow: "auto" }}>
                  {JSON.stringify({ final_prompts: item.final_prompts, style_baseline: item.style_baseline, reference_images: item.reference_images }, null, 2)}
                </pre>
              </details>
              {item.error_message && <div className="panel-sub" style={{ color: "var(--red)" }}>失败原因：{String(item.error_message)}</div>}
            </div>
          ))}
        </>}
      </section>
    </div>
  );
}
