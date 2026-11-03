import { Platform, platformMeta, ALL_PLATFORMS } from "./platforms";

export interface NavItem {
  page: string;
  label: string;
  /** demo 的克制符号体系（▦ ✳ ◎ ▤ ▣ ⌁ ⎇ ⚙），不用 emoji */
  icon: string;
  section: string;
  /** 不在侧栏出现（如首启向导，入口在顶栏） */
  hidden?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { page: "accounts",   label: "账号与登录", icon: "◉", section: "账号管理" },
  { page: "dashboard",  label: "运营总览",   icon: "▦", section: "指挥与发现" },
  { page: "activation", label: "快速体验",   icon: "✧", section: "指挥与发现", hidden: true },
  { page: "global-ops", label: "全局运营中心", icon: "▥", section: "指挥与发现", hidden: true },
  { page: "hot",        label: "热点雷达",   icon: "✳", section: "指挥与发现" },
  { page: "goals",      label: "目标与策略", icon: "◎", section: "指挥与发现" },

  { page: "production", label: "内容生产",   icon: "▤", section: "内容作战" },
  { page: "review",     label: "集中审核",   icon: "✓", section: "内容作战" },
  { page: "plan-center", label: "计划管理", icon: "▧", section: "内容作战" },
  { page: "calendar",   label: "内容日历",   icon: "▦", section: "内容作战" },
  { page: "assets",     label: "内容资产",   icon: "▣", section: "内容作战" },

  { page: "operations", label: "运营控制台", icon: "⌁", section: "运营与归因" },
  { page: "analytics",  label: "数据复盘",   icon: "⌁", section: "运营与归因" },
  { page: "gaps",       label: "缺口与实验", icon: "◇", section: "运营与归因" },

  { page: "agent",      label: "Agent 编排与诊断", icon: "⎇", section: "Agent 中枢" },
  { page: "task-queue", label: "全局任务队列", icon: "⇅", section: "Agent 中枢", hidden: true },
  { page: "onboarding", label: "新手引导 / 快速配置", icon: "☷", section: "Agent 中枢", hidden: true },
];

interface Props {
  current: string;
  onChange: (page: string) => void;
  brokenPlatforms: Platform[];
  loggedIn: Platform[];
  enabled: Platform[];
  activeRounds?: number;
  /** 首屏状态未回来前显示「检测中」，避免闪一下「未登录」 */
  statusLoading?: boolean;
}

export default function Sidebar({ current, onChange, brokenPlatforms, loggedIn, enabled, activeRounds, statusLoading }: Props) {
  const sections: Record<string, NavItem[]> = {};
  NAV_ITEMS.filter((n) => !n.hidden).forEach((item) => {
    (sections[item.section] = sections[item.section] || []).push(item);
  });

  const countFor = (page: string): number | null => {
    if (page === "operations" && brokenPlatforms.length) return brokenPlatforms.length;
    if (page === "production" && activeRounds) return activeRounds;
    if (page === "accounts") {
      // 角标口径与账号页一致：账号页固定展示四个平台卡片，
      // 因此这里也按四个平台统计未登录数（不再只算已启用平台，否则会出现「4 个账号只提示 2 个未登录」）。
      const missing = ALL_PLATFORMS.filter((p) => !loggedIn.includes(p)).length;
      return missing ? missing : null;
    }
    return null;
  };

  // 底部平台状态始终列出四个受支持平台：账号页也是四张卡，
  // 若这里只显示「已启用」平台，未启用的平台会整行消失，导致「四个账号只提示三个未登录」的误读。
  const platforms = ALL_PLATFORMS;

  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">I</div>
        <div>
          <div className="brand-name">IterWave</div>
          <div className="brand-sub">多平台内容运营工作台</div>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="页面">
        {Object.entries(sections).map(([section, items]) => (
          <div key={section} role="group" aria-label={section}>
            <div className="group-label">{section}</div>
            {items.map((item) => {
              const count = countFor(item.page);
              const active = current === item.page;
              return (
                <button
                  type="button"
                  key={item.page}
                  className={`nav ${active ? "active" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onChange(item.page)}
                  title={item.label}
                >
                  <span className="ni" aria-hidden="true">{item.icon}</span>
                  <span className="label">{item.label}</span>
                  {count != null && <span className="count" title={item.page === "accounts" ? `${count} 个平台尚未登录` : undefined}>{item.page === "accounts" ? `${count} 未登录` : count}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="side-bottom">
        <div className="health-title">平台账号状态</div>
        {platforms.map((p) => {
          const m = platformMeta(p);
          const isEnabled = enabled.includes(p);
          const isBroken = brokenPlatforms.includes(p);
          const isLogged = loggedIn.includes(p);
          // 「未登录」优先展示：角标按四个平台统计未登录数，这里同步显示，
          // 避免角标 3 而列表只有 2 行「未登录」的矛盾；已登录但未启用的平台仍标「未启用」。
          const dot = statusLoading || !isLogged ? "bad" : !isEnabled || isBroken ? "warn" : "";
          const label = statusLoading
            ? "检测中"
            : !isLogged
              ? "未登录"
              : !isEnabled
                ? "未启用"
                : isBroken
                  ? "熔断冷却"
                  : "已登录";
          return (
            <button
              type="button"
              key={p}
              className="health-row health-btn"
              onClick={() => onChange("accounts")}
              aria-label={`${m.label}：${label}，打开账号与登录`}
            >
              <span className="health-left"><i className={`dot ${dot}`} aria-hidden="true" />{m.label}</span>
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
