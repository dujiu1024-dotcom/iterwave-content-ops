import { useCallback, useEffect, useState } from "react";
import AsyncPanel from "../components/AsyncPanel";
import EmptyHint from "../components/EmptyHint";
import TopicPoolPane from "../components/TopicPoolPane";
import KbSourcesPanel from "../components/KbSourcesPanel";
import IndustryPresetPicker from "../components/IndustryPresetPicker";
import CreatedContentPane from "../components/CreatedContentPane";
import MediaLibraryPane from "../components/MediaLibraryPane";
import { useApp } from "../AppContext";
import { PersonaEditor } from "../components/ConfigEditors";
import { navigate, useUrlTab } from "../nav";
import {
  api, AssetItem, AssetType, Draft, HotTopicItem, KbInspect, ScoredTopic, SystemConfigView,
} from "../api";

type Tab = "topics" | "scoring" | "created" | "media" | "sources" | "voice" | "industry" | "templates" | "mapping";

const TABS: { key: Tab; label: string }[] = [
  { key: "topics", label: "选题池" },
  { key: "scoring", label: "选题评分" },
  { key: "created", label: "已生产内容" },
  { key: "media", label: "图片素材库" },
  { key: "sources", label: "外部参考" },
  { key: "voice", label: "个人观点与画像" },
  { key: "industry", label: "行业预设" },
  { key: "templates", label: "内容模板" },
  { key: "mapping", label: "知识库来源" },
];

export default function Assets() {
  const { showToast, withPending, isPending } = useApp();
  const [tab, setTab] = useUrlTab<Tab>(TABS.map((t) => t.key), "topics");
  const [scored, setScored] = useState<{ mode: string; topics: ScoredTopic[] } | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [kb, setKb] = useState<KbInspect | null>(null);
  const [config, setConfig] = useState<SystemConfigView | null>(null);
  const [hot, setHot] = useState<HotTopicItem[]>([]);
  const [assets, setAssets] = useState<Record<AssetType, AssetItem[]>>({ reference: [], voice: [], template: [] });
  const [assetPages, setAssetPages] = useState<Record<AssetType, number>>({ reference: 1, voice: 1, template: 1 });
  const [assetTotals, setAssetTotals] = useState<Record<AssetType, number>>({ reference: 0, voice: 0, template: 0 });
  const [assetPageCounts, setAssetPageCounts] = useState<Record<AssetType, number>>({ reference: 1, voice: 1, template: 1 });
  const [editingTemplate, setEditingTemplate] = useState<AssetItem | null>(null);
  const [viewingTemplateId, setViewingTemplateId] = useState<number | null>(null);
  const [templateVersions, setTemplateVersions] = useState<Array<{ version: number; title: string; body: string | null; created_at: string | null }>>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Promise.allSettled — 各请求独立失败不阻断其他成功结果
      // 尤其 hotTopics 是外部公益 API（易挂），不能让它拖垮整个页面
      const results = await Promise.allSettled([
        api.scoreTopics(30),
        api.draftsAll(),
        api.kbInspect(),
        api.config(),
        api.hotTopics(),
        api.assetsPage("reference", 1, 20),
        api.assetsPage("voice", 1, 20),
        api.assetsPage("template", 1, 20, true),
      ]);
      // 成功的逐个 set；失败的汇总成 error 提示
      const failures: string[] = [];
      const ok = (i: number) => results[i].status === "fulfilled";
      const v = (i: number) => ok(i) ? (results[i] as PromiseFulfilledResult<any>).value : null;
      if (!ok(0)) failures.push("选题评分"); else setScored(v(0));
      if (!ok(1)) failures.push("草稿列表"); else setDrafts(v(1));
      if (!ok(2)) failures.push("题库检查"); else setKb(v(2));
      if (!ok(3)) failures.push("配置"); else setConfig(v(3));
      if (!ok(4)) failures.push("热点（外部公益 API）"); else setHot(v(4)?.items ?? []);
      if (!ok(5)) failures.push("外部参考资产");
      if (!ok(6)) failures.push("个人画像资产");
      if (!ok(7)) failures.push("模板资产");

      const refs = v(5); const voices = v(6); const templates = v(7);
      setAssets({
        reference: refs?.items ?? [],
        voice: voices?.items ?? [],
        template: templates?.items ?? [],
      });
      setAssetPages({ reference: 1, voice: 1, template: 1 });
      setAssetTotals({
        reference: refs?.total ?? 0,
        voice: voices?.total ?? 0,
        template: templates?.total ?? 0,
      });
      setAssetPageCounts({
        reference: refs?.pages ?? 1,
        voice: voices?.pages ?? 1,
        template: templates?.pages ?? 1,
      });

      setAssetTotals({
        reference: refs?.total ?? 0,
        voice: voices?.total ?? 0,
        template: templates?.total ?? 0,
      });
      setAssetPages({
        reference: refs?.pages ?? 1,
        voice: voices?.pages ?? 1,
        template: templates?.pages ?? 1,
      });

      if (failures.length > 0) {
        setError(`部分数据加载失败：${failures.join("、")} — 已展示成功部分`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [referencePage, voicePage, templatePage]);

  useEffect(() => { void load(); }, [load]);

  const loadAssetPage = async (type: AssetType, page: number) => {
    try {
      const result = await api.assetsPage(type, page, 20, type === "template");
      setAssets((current) => ({ ...current, [type]: result.items }));
      setAssetPages((current) => ({ ...current, [type]: result.page }));
      setAssetTotals((current) => ({ ...current, [type]: result.total }));
      setAssetPageCounts((current) => ({ ...current, [type]: result.pages }));
    } catch (e) {
      showToast(`资产列表加载失败：${e instanceof Error ? e.message : String(e)}`, "error");
    }
  };

  const addAsset = async (type: AssetType, data: { title: string; body?: string; source_url?: string }) => {
    try {
      await withPending(`asset:${type}`, () => api.createAsset({ asset_type: type, ...data }));
      showToast("✓ 资产已保存", "success");
      await load();
      return true;
    } catch (e: any) {
      showToast(`保存失败: ${e?.message ?? e}`, "error");
      return false;
    }
  };

  const openTemplateVersions = async (id: number) => {
    try {
      const result = await api.assetVersions(id);
      setTemplateVersions(result.items);
      setViewingTemplateId(id);
    } catch (e) {
      showToast(`模板版本历史加载失败：${e instanceof Error ? e.message : String(e)}`, "error");
    }
  };

  const restoreTemplateVersion = async (id: number, version: number) => {
    try {
      await api.restoreAssetVersion(id, version);
      showToast(`已从 v${version} 恢复；系统创建了新的版本记录`, "success");
      setViewingTemplateId(null);
      await load();
    } catch (e) {
      showToast(`模板版本恢复失败：${e instanceof Error ? e.message : String(e)}`, "error");
    }
  };

  const total = kb?.stats.total;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">内容资产</div>
          <h1 className="page-title">内容资产</h1>
          <div className="page-desc">
            选题库、已生产内容、外部参考、个人观点与画像、内容模板、字段映射统一归档。
            
          </div>
        </div>
        <div className="head-actions">
          <button className="btn sm" onClick={load} disabled={loading}>↻ 刷新</button>
        </div>
      </div>

      {error && (
        <div className="panel" style={{ borderColor: "var(--red)", marginBottom: 14 }}>
          <div className="panel-sub" style={{ color: "var(--red)", display: "flex", gap: 10, alignItems: "center" }}>
            <span>! 加载失败：{error}</span>
            <button className="btn sm" onClick={load}>↻ 重试</button>
          </div>
        </div>
      )}

      <div className="tabs page-tabs">
        {TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? "active" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "topics" && <TopicPoolPane />}

      {tab === "scoring" && (
        <>
          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">★</span>选题评分排行</div>
                <div className="panel-sub">当前评分模式：{({ cold_start: "冷启动", sample_accumulating: "样本积累", stable: "稳定运行" } as Record<string, string>)[scored?.mode ?? ""] ?? "—"} · 按定位、时效、新鲜度加权</div>
              </div>
            </div>
            <AsyncPanel
              loading={loading}
              error={error}
              onRetry={load}
              empty={(scored?.topics.length ?? 0) === 0}
              emptyText={
                <EmptyHint
                  className=""
                  next="到「内容资产 → 知识库来源」检查并同步来源，再到「内容生产」新建一轮"
                >
                  评分引擎暂无输出
                </EmptyHint>
              }
              loadingText="加载评分结果..."
              boxed={false}
            >
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th><th>选题</th><th>维度</th><th>总分</th>
                      <th>定位</th><th>时效</th><th>新鲜度</th><th>类型</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(scored?.topics ?? []).map((t, i) => (
                      <tr key={t.id}>
                        <td style={{ color: "var(--subtle)", fontSize: 11, fontWeight: 600 }}>#{i + 1}</td>
                        <td style={{ fontWeight: 500 }}>{t.title}</td>
                        <td><span className="tag gray">{t.dimension}</span></td>
                        <td style={{ fontWeight: 700, color: "var(--blue)", fontSize: 14 }}>{t.total_score}</td>
                        <td style={{ fontSize: 11 }}>{_num(t.breakdown.position_match?.raw)}</td>
                        <td style={{ fontSize: 11 }}>{_num(t.breakdown.timeliness?.raw)}</td>
                        <td style={{ fontSize: 11 }}>{_num(t.breakdown.freshness?.raw)}</td>
                        <td>{t.is_exploration ? <span className="tag purple">探索</span> : <span className="tag gray">常规</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </AsyncPanel>
          </div>
        </>
      )}

      {tab === "created" && <CreatedContentPane />}
      {tab === "media" && <MediaLibraryPane />}

      {tab === "sources" && (
        <>
          <AssetForm
            type="reference"
            titleLabel="来源标题"
            needUrl
            pending={isPending("asset:reference")}
            onSubmit={addAsset}
          />
          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">↗</span>外部参考 · 已录入 {assetTotals.reference} 条</div>
                <div className="panel-sub">来源必须可溯源（原始链接 + 发现时间 + 证据状态），不存无法溯源的摘要</div>
              </div>
            </div>
            <AsyncPanel
              loading={loading}
              error={error}
              onRetry={load}
              empty={assets.reference.length === 0}
              emptyText={
                <EmptyHint className="" next="在上方粘贴新闻/文章链接并填写标题，或到「热点雷达」查看已采集热点">
                  暂无外部参考
                </EmptyHint>
              }
              loadingText="加载外部参考..."
              boxed={false}
            >
              <div className="table-wrap">
                <table>
                  <thead><tr><th>标题</th><th>原始链接</th><th>录入时间</th></tr></thead>
                  <tbody>
                    {assets.reference.map((a) => (
                      <tr key={a.id}>
                        <td>{a.title}</td>
                        <td style={{ fontSize: 11 }}>
                          {a.source_url ?? <span style={{ color: "var(--subtle)" }}>—</span>}
                        </td>
                        <td>{a.created_at ?? <span style={{ color: "var(--subtle)" }}>—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <AssetPagination page={referencePage} pages={assetPages.reference} total={assetTotals.reference}
                onPageChange={setReferencePage} pageSize={assetPageSize} />
            </AsyncPanel>
          </div>

          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">✳</span>热点采集来源（实时）</div>
                <div className="panel-sub">采集失败的来源不显示热度数字</div>
              </div>
            </div>
            {hot.length === 0 ? (
              <EmptyHint next="到「热点雷达」点「立即刷新」触发采集">暂无热点数据</EmptyHint>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>标题</th><th>来源</th><th>发现时间</th><th>热度</th><th>证据状态</th></tr></thead>
                  <tbody>
                    {hot.slice(0, 10).map((h) => (
                      <tr key={h.id}>
                        <td>{h.title}</td>
                        <td><span className="tag gray">{h.source}</span></td>
                        <td>{h.discovered_at ?? <span style={{ color: "var(--subtle)" }}>—</span>}</td>
                        <td>{h.heat == null ? <span style={{ color: "var(--subtle)" }}>—</span> : h.heat.toLocaleString()}</td>
                        <td>
                          <span className={`tag ${h.evidence_status === "collected" ? "green" : h.evidence_status === "demo" ? "orange" : "red"}`}>
                            {h.evidence_status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {tab === "voice" && (
        <>
          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">♙</span>账号画像</div>
                <div className="panel-sub">全局与各平台画像统一在「目标与策略 → 账号定位与画像」维护</div>
              </div>
              <button className="btn sm" onClick={() => navigate("goals", "persona")}>去编辑 →</button>
            </div>
            {!config ? (
              <div className="note">加载配置中…</div>
            ) : (
              <div className="list">
                <div className="task-row"><i className="task-prio blue" /><div className="task-main"><div className="task-title">一句话定位</div><div className="task-desc">{config.readonly.positioning || "未填写"}</div></div></div>
                <div className="task-row"><i className="task-prio green" /><div className="task-main"><div className="task-title">定位关键词</div>
                  <div className="flex wrap" style={{ gap: 6, marginTop: 4 }}>{config.readonly.persona_keywords.length ? config.readonly.persona_keywords.map((k) => <span key={k} className="tag blue">{k}</span>) : <span className="tag gray">未配置</span>}</div></div></div>
                <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">禁止方向</div>
                  <div className="flex wrap" style={{ gap: 6, marginTop: 4 }}>{config.readonly.forbidden_directions.length ? config.readonly.forbidden_directions.map((k) => <span key={k} className="tag red">{k}</span>) : <span className="tag gray">未配置</span>}</div></div></div>
              </div>
            )}
            <div className="note" style={{ marginTop: 12 }}>
              红线关键词 {config?.readonly.redline_keywords_count ?? "—"} 个（出现即丢弃重生成，不可关闭）
            </div>
          </div>

          <AssetForm
            type="voice"
            titleLabel="观点标题"
            bodyPlaceholder="例如：重要的不是把答案给得更快，而是帮读者把问题想清楚。"
            pending={isPending("asset:voice")}
            onSubmit={addAsset}
          />
          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">✎</span>个人观点库 · {assetTotals.voice} 条</div>
                <div className="panel-sub">真实录入，不预置演示观点</div>
              </div>
            </div>
            {assets.voice.length === 0 ? (
              <EmptyHint next="在上方录入一条你真实的立场或判断，后续文案会参考它">暂无个人观点</EmptyHint>
            ) : (
              <div className="list">
                {assets.voice.map((v) => (
                  <div className="task-row" key={v.id}>
                    <i className="task-prio purple" />
                    <div className="task-main">
                      <div className="task-title">{v.title}</div>
                      <div className="task-desc">{v.body || <span style={{ color: "var(--subtle)" }}>（无正文）</span>}</div>
                    </div>
                    <span className="tag gray">{v.created_at?.slice(0, 10) ?? "—"}</span>
                  </div>
                ))}
              </div>
            )}
            <AssetPagination page={voicePage} pages={assetPages.voice} total={assetTotals.voice}
              onPageChange={setVoicePage} pageSize={assetPageSize} />
          </div>
        </>
      )}

      {tab === "industry" && (
        <>
          <div className="panel">
            <div className="panel-title">行业预设</div>
            <div className="panel-sub">选择适合当前账号组的行业配置模板；应用后会更新目标与策略配置。</div>
          </div>
          <IndustryPresetPicker onApplied={load} />
        </>
      )}

      {tab === "templates" && (
        <>
          <div className="panel template-howto">
            <div className="panel-title">内容模板是 AI 生成时使用的“文章结构说明”</div>
            <div className="panel-sub" style={{ marginTop: 6 }}>
              它不是选题、不是完整文案，也不是行业画像。它告诉 AI 一篇内容按什么顺序组织，例如“问题 → 真实经历 → 方法 → 证据 → 局限 → 结论”。
            </div>
            <div className="template-howto-steps">
              <span><b>1</b> 新建或添加示例模板</span>
              <span><b>2</b> 在下方模板卡片点击“编辑模板”修改结构</span>
              <span><b>3</b> 到“内容生产 → 生产线”选择模板</span>
              <span><b>4</b> 本轮 AI 会参考结构生成，之后修改只影响新生产</span>
            </div>
            <div className="flex wrap" style={{ gap: 8, marginTop: 10 }}>
              <button className="btn sm soft" type="button" onClick={() => void addAsset("template", { title: "知识科普结构", body: "1. 用一个具体问题开场\n2. 解释核心概念\n3. 分步骤讲清方法\n4. 给出真实案例或证据\n5. 说明适用边界\n6. 总结可执行建议" })}>＋ 添加知识科普示例</button>
              <button className="btn sm soft" type="button" onClick={() => void addAsset("template", { title: "产品体验测评结构", body: "1. 先说使用场景与痛点\n2. 说明体验条件\n3. 按关键维度逐项体验\n4. 展示优点与不足\n5. 给出适合/不适合的人群\n6. 总结购买或使用建议" })}>＋ 添加体验测评示例</button>
              <button className="btn sm" type="button" onClick={() => navigate("production")}>去内容生产选择模板 →</button>
            </div>
          </div>
          {editingTemplate && <TemplateEditor item={editingTemplate} onCancel={() => setEditingTemplate(null)} onSaved={async () => { setEditingTemplate(null); await load(); }} />}
          <AssetForm
            type="template"
            titleLabel="模板"
            bodyPlaceholder="例如：问题引入 → 实测过程 → 结果证据 → 局限 → 结论"
            pending={isPending("asset:template")}
            onSubmit={addAsset}
          />
          {viewingTemplateId !== null && (
            <div className="panel">
              <div className="panel-head">
                <div>
                  <div className="panel-title">模板版本历史</div>
                  <div className="panel-sub">恢复历史版本会创建一个新的版本号，不会覆盖已有历史。</div>
                </div>
                <button className="btn xs" type="button" onClick={() => setViewingTemplateId(null)}>关闭</button>
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>版本</th><th>名称</th><th>结构说明</th><th>创建时间</th><th>操作</th></tr></thead>
                  <tbody>
                    {templateVersions.map((version) => (
                      <tr key={version.version}>
                        <td>v{version.version}</td>
                        <td>{version.title}</td>
                        <td style={{ whiteSpace: "pre-line" }}>{version.body || "—"}</td>
                        <td>{version.created_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                        <td><button className="btn xs" type="button" onClick={() => void restoreTemplateVersion(viewingTemplateId, version.version)}>恢复此版本</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div className="panel">
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">▧</span>可复用内容模板 · {assetTotals.template} 条</div>
                <div className="panel-sub">模板用于统一本轮内容的表达结构。保存后，在「内容生产 → 生产线 → 内容结构模板」中选择，系统会将模板结构注入本轮 AI 生成提示词。</div>
              </div>
            </div>
            {assets.template.length === 0 ? (
              <EmptyHint next="先添加一套结构，例如「痛点引入 → 真实体验 → 证据 → 局限 → 结论」，然后在内容生产的生产线中选择它。">暂无模板</EmptyHint>
            ) : (
              <div className="grid three">
                {assets.template.map((t) => {
                  // 后端统一返回 bool；兼容旧数据的 0/1，缺省视为启用。
                  const inactive = t.is_active !== undefined && t.is_active !== null && !Number(t.is_active);
                  return (
                  <div className="skill-card" key={t.id} style={{ opacity: inactive ? 0.62 : 1 }}>
                    <div className="skill-name">{t.title} {inactive && <span className="tag gray">已停用</span>}</div>
                    <div className="skill-desc" style={{ whiteSpace: "pre-line" }}>{t.body || "（无结构说明）"}</div>
                    <div className="skill-foot">
                      <span>v{t.version ?? 1} · {t.updated_at?.slice(0, 10) ?? t.created_at?.slice(0, 10) ?? "—"}</span>
                      <button className="btn xs" type="button" onClick={() => setEditingTemplate(t)}>编辑模板</button>
                      <button className="btn xs" type="button" onClick={() => void openTemplateVersions(t.id)}>版本历史</button>
                      {inactive ? (
                        <button className="btn xs" type="button" onClick={async () => { await api.restoreAsset(t.id); await loadAssetPage("template", assetPages.template); }}>恢复</button>
                      ) : (
                        <button className="btn xs" type="button" onClick={async () => { await api.archiveAsset(t.id); await loadAssetPage("template", assetPages.template); }}>停用</button>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
            <AssetPagination page={templatePage} pages={assetPages.template} total={assetTotals.template}
              onPageChange={setTemplatePage} pageSize={assetPageSize} />
          </div>
        </>
      )}

      {(tab === "sources" || tab === "voice" || tab === "templates") && (() => {
        const type: AssetType = tab === "sources" ? "reference" : tab === "voice" ? "voice" : "template";
        const page = assetPages[type];
        const pages = assetPageCounts[type];
        return assetTotals[type] > 0 ? (
          <div className="panel flex between center" style={{ gap: 10, flexWrap: "wrap" }}>
            <span className="note">共 {assetTotals[type]} 条 · 第 {page}/{pages} 页 · 每页 20 条</span>
            <div className="flex" style={{ gap: 6 }}>
              <button className="btn sm" disabled={page <= 1} onClick={() => void loadAssetPage(type, page - 1)}>上一页</button>
              <button className="btn sm" disabled={page >= pages} onClick={() => void loadAssetPage(type, page + 1)}>下一页</button>
            </div>
          </div>
        ) : null;
      })()}
      {tab === "mapping" && <KbSourcesPanel />}
    </>
  );
}

/** 三类资产共用的小表单（只提交真实输入，不做假数据填充） */
function AssetForm({
  type, titleLabel, bodyPlaceholder, needUrl, pending, onSubmit,
}: {
  type: AssetType;
  titleLabel: string;
  bodyPlaceholder?: string;
  needUrl?: boolean;
  pending: boolean;
  onSubmit: (type: AssetType, data: { title: string; body?: string; source_url?: string }) => Promise<boolean>;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [url, setUrl] = useState("");

  const submit = async () => {
    if (!title.trim()) return;
    const ok = await onSubmit(type, {
      title: title.trim(),
      body: body.trim() || undefined,
      source_url: needUrl ? url.trim() : undefined,
    });
    if (ok) { setTitle(""); setBody(""); setUrl(""); }
  };

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <div className="panel-head">
        <div>
          <div className="panel-title"><span className="icon">＋</span>{type === "template" ? "新建模板" : `新增${titleLabel}`}</div>
          <div className="panel-sub">保存后可在内容生产的生产线中直接选择</div>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: needUrl ? "1fr 1fr 1.4fr auto" : "1fr 2fr auto", gap: 10 }}>
        <input className="field" placeholder={titleLabel} value={title} onChange={(e) => setTitle(e.target.value)} />
        {type === "template" ? (
          <textarea className="field" rows={3} placeholder={bodyPlaceholder ?? "按步骤写出内容结构，每行一个环节"} value={body} onChange={(e) => setBody(e.target.value)} />
        ) : (
          <input className="field" placeholder={bodyPlaceholder ?? "说明（可空）"} value={body} onChange={(e) => setBody(e.target.value)} />
        )}
        {needUrl && <input className="field" placeholder="原始链接 https://…（必填）" value={url} onChange={(e) => setUrl(e.target.value)} />}
        <button className="btn primary sm" disabled={pending || !title.trim() || (needUrl && !url.trim())} onClick={submit}>
          {pending ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}

function _num(v: number | null | undefined) {
  return v == null ? <span style={{ color: "var(--subtle)" }}>—</span> : v.toFixed(0);
}


function TemplateEditor({ item, onCancel, onSaved }: {
  item: AssetItem; onCancel: () => void; onSaved: () => void | Promise<void>;
}) {
  const { showToast } = useApp();
  const [title, setTitle] = useState(item.title);
  const [body, setBody] = useState(item.body ?? "");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!title.trim() || !body.trim()) {
      showToast("模板名称和结构说明不能为空", "error");
      return;
    }
    setBusy(true);
    try {
      await api.updateAsset(item.id, { asset_type: "template", title: title.trim(), body: body.trim() });
      showToast("内容模板已更新；后续生产将使用新结构", "success");
      await onSaved();
    } catch (e) {
      showToast(`更新失败：${(e as Error).message}`, "error");
    } finally { setBusy(false); }
  };
  return (
    <div className="panel template-editor" style={{ marginBottom: 14, borderColor: "var(--blue)" }}>
      <div className="panel-head">
        <div><div className="panel-title">编辑内容模板</div><div className="panel-sub">只修改模板结构，不会回写已生成的内容。</div></div>
        <button className="btn sm" type="button" onClick={onCancel}>取消</button>
      </div>
      <label className="form-label">模板名称<input className="field" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="form-label">内容结构（建议每行一个环节）<textarea className="field" rows={5} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <div className="flex" style={{ justifyContent: "flex-end" }}>
        <button className="btn sm primary" type="button" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存修改"}</button>
      </div>
    </div>
  );
}

