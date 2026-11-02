import { useEffect, useState } from "react";
import { api, type IndustryPreset } from "../api";
import { useApp } from "../AppContext";

/** 行业模板：内置模板可应用；自定义模板可新增并持久化到当前账号组。 */
export default function IndustryPresetPicker({ onApplied }: { onApplied: () => void }) {
  const { showToast } = useApp();
  const [presets, setPresets] = useState<IndustryPreset[]>([]);
  const [active, setActive] = useState<IndustryPreset | null>(null);
  const [busy, setBusy] = useState("");
  const [applied, setApplied] = useState<{ dimensions: string[]; notes: Record<string, string> } | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [positioning, setPositioning] = useState("");
  const [keywords, setKeywords] = useState("");
  const [forbidden, setForbidden] = useState("");
  const [redlines, setRedlines] = useState("");
  const [dimensions, setDimensions] = useState("");
  const [platformNotes, setPlatformNotes] = useState("{}");

  const load = async () => {
    const res = await api.presets();
    setPresets(res.items ?? []);
  };
  useEffect(() => {
    let alive = true;
    api.presets().then((res) => { if (alive) setPresets(res.items ?? []); })
      .catch((e) => showToast((e as Error).message, "error"));
    return () => { alive = false; };
  }, [showToast]);

  const apply = async (preset: IndustryPreset) => {
    if (!window.confirm(`应用「${preset.name}」模板？会更新当前账号组的定位、关键词、禁止方向并追加合规红线。`)) return;
    setBusy(preset.key);
    setActive(preset);
    try {
      const res = await api.applyPreset(preset.key);
      setApplied({ dimensions: res.dimensions, notes: res.platform_notes });
      showToast(res.message, "success");
      onApplied();
    } catch (e) { showToast((e as Error).message, "error"); }
    finally { setBusy(""); }
  };

  const beginEdit = (preset: IndustryPreset) => {
    if (preset.is_builtin) return;
    setEditingKey(preset.key);
    setKey(preset.key);
    setName(preset.name);
    setDescription(preset.description);
    setPositioning(preset.positioning);
    setKeywords(preset.persona_keywords.join("\n"));
    setForbidden(preset.forbidden_directions.join("\n"));
    setRedlines(preset.extra_redlines.join("\n"));
    setDimensions(preset.dimensions.join("\n"));
    setPlatformNotes(JSON.stringify(preset.platform_notes ?? {}, null, 2));
    setShowEditor(true);
  };

  const resetEditor = () => {
    setShowEditor(false);
    setEditingKey(null);
    setKey(""); setName(""); setDescription(""); setPositioning(""); setKeywords("");
    setForbidden(""); setRedlines(""); setDimensions(""); setPlatformNotes("{}");
  };

  const deletePreset = async (preset: IndustryPreset) => {
    if (preset.is_builtin || preset.is_active === false || !window.confirm(`归档自定义行业模板「${preset.name}」？历史版本会保留，可随时恢复。`)) return;
    setBusy(preset.key);
    try {
      await api.deletePreset(preset.key);
      if (editingKey === preset.key) resetEditor();
      await load();
      showToast("自定义行业模板已归档，历史版本已保留", "success");
    } catch (e) {
      showToast(`归档失败：${(e as Error).message}`, "error");
    } finally { setBusy(""); }
  };

  const restorePreset = async (preset: IndustryPreset) => {
    setBusy(preset.key);
    try {
      await api.restorePreset(preset.key);
      await load();
      showToast("行业模板已恢复", "success");
    } catch (e) { showToast(`恢复失败：${(e as Error).message}`, "error"); }
    finally { setBusy(""); }
  };

  const saveCustom = async () => {
    const safeKey = key.trim().toLowerCase().replace(/\s+/g, "-");
    if (!safeKey || !name.trim() || !positioning.trim()) {
      showToast("模板标识、名称和账号定位不能为空", "error");
      return;
    }
    let notes: Record<string, string>;
    try {
      const parsed: unknown = JSON.parse(platformNotes);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      notes = parsed as Record<string, string>;
      if (Object.values(notes).some((v) => typeof v !== "string")) throw new Error();
    } catch {
      showToast("平台说明必须是 JSON 对象，例如 {\"kuaishou\":\"说明\"}", "error");
      return;
    }
    setBusy("save");
    try {
      await api.savePreset(safeKey, {
        name: name.trim(), description: description.trim(), positioning: positioning.trim(),
        persona_keywords: keywords.split(/[，,\n]/).map((v) => v.trim()).filter(Boolean),
        forbidden_directions: forbidden.split(/[，,\n]/).map((v) => v.trim()).filter(Boolean),
        extra_redlines: redlines.split(/[，,\n]/).map((v) => v.trim()).filter(Boolean),
        dimensions: dimensions.split(/[，,\n]/).map((v) => v.trim()).filter(Boolean),
        platform_notes: notes,
      });
      await load();
      resetEditor();
      showToast(editingKey ? "自定义行业模板已更新" : "自定义行业模板已保存，可立即应用", "success");
    } catch (e) { showToast(`保存失败：${(e as Error).message}`, "error"); }
    finally { setBusy(""); }
  };

  const fillPlan = async () => {
    if (!applied) return;
    try {
      await api.adoptGaps(applied.dimensions);
      showToast("已写入母计划维度，下游内容会按新维度重排", "success");
    } catch (e) { showToast(`写入母计划失败：${(e as Error).message}`, "error"); }
  };

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-head">
        <div>
          <div className="panel-title"><span className="icon">◇</span>行业模板</div>
          <div className="panel-sub">选择模板可快速填入账号定位、关键词、禁止方向和合规红线。自定义模板保存在当前账号组。</div>
        </div>
        <button className="btn sm primary" type="button" onClick={() => setShowEditor((v) => !v)}>
          {showEditor ? "收起编辑" : "＋ 新增行业模板"}
        </button>
      </div>
      {showEditor && (
        <div className="industry-preset-editor">
          <div className="grid two">
            <label>模板标识（英文）<input className="field" value={key} disabled={!!editingKey} onChange={(e) => setKey(e.target.value)} placeholder="例如 local-food" /></label>
            <label>模板名称<input className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 本地餐饮" /></label>
          </div>
          <label>模板说明<input className="field" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="适用场景" /></label>
          <label>账号定位<textarea className="field" rows={2} value={positioning} onChange={(e) => setPositioning(e.target.value)} placeholder="这个行业账号要为谁解决什么问题" /></label>
          <div className="grid two">
            <label>定位关键词（每行一项）<textarea className="field" rows={3} value={keywords} onChange={(e) => setKeywords(e.target.value)} /></label>
            <label>禁止方向（每行一项）<textarea className="field" rows={3} value={forbidden} onChange={(e) => setForbidden(e.target.value)} /></label>
            <label>合规红线（每行一项）<textarea className="field" rows={3} value={redlines} onChange={(e) => setRedlines(e.target.value)} /></label>
            <label>建议内容维度（每行一项）<textarea className="field" rows={3} value={dimensions} onChange={(e) => setDimensions(e.target.value)} /></label>
          </div>
          <label>平台说明 JSON（可留空）<textarea className="field" rows={2} value={platformNotes} onChange={(e) => setPlatformNotes(e.target.value)} spellCheck={false} /></label>
          <div className="flex wrap" style={{ gap: 8, justifyContent: "flex-end" }}>
            <button className="btn sm" type="button" onClick={resetEditor}>取消</button>
            <button className="btn sm primary" type="button" disabled={busy === "save"} onClick={() => void saveCustom()}>{busy === "save" ? "保存中…" : "保存模板"}</button>
          </div>
        </div>
      )}
      <div className="industry-preset-list">
        {presets.map((p) => (
          <div key={p.key} className="flex wrap" style={{ gap: 5, alignItems: "center" }}>
            <button className={p.is_builtin ? "btn sm soft" : "btn sm"} type="button" disabled={busy === p.key || p.is_active === false}
              title={p.is_active === false ? "已归档，恢复后可应用" : p.description} onClick={() => void apply(p)}>
              {busy === p.key ? "处理中…" : p.name}{!p.is_builtin && " · 自定义"}{p.is_active === false && " · 已归档"}
            </button>
            {!p.is_builtin && p.is_active !== false && <>
              <button className="btn xs" type="button" disabled={busy !== ""} onClick={() => beginEdit(p)}>编辑</button>
              <button className="btn xs danger" type="button" disabled={busy !== ""} onClick={() => void deletePreset(p)}>归档</button>
            </>}
            {!p.is_builtin && p.is_active === false &&
              <button className="btn xs primary" type="button" disabled={busy !== ""} onClick={() => void restorePreset(p)}>恢复</button>}
          </div>
        ))}
        {presets.length === 0 && <div className="empty-state">还没有行业模板，请先新增一套。</div>}
      </div>
      {active && applied && (
        <div className="note" style={{ marginTop: 12, lineHeight: 1.8 }}>
          <strong>{active.name}</strong> 已应用。建议维度：{applied.dimensions.join("、")}
          <button className="btn xs soft" type="button" style={{ marginLeft: 8 }} onClick={() => void fillPlan()}>填入母计划</button>
          {Object.entries(applied.notes).length > 0 && <div style={{ marginTop: 6, color: "var(--muted)" }}>
            平台限制：{Object.entries(applied.notes).map(([k, v]) => <div key={k}>· {k}：{v}</div>)}
          </div>}
        </div>
      )}
    </div>
  );
}
