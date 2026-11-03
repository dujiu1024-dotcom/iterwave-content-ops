/**
 * Sanitize backend error details before they reach user-visible UI.
 * Keep safe business messages; suppress implementation and credential details.
 */
const INTERNAL_DETAIL_PATTERNS = [
  /\btraceback\b/i,
  /File ["'][^"']+["'], line \d+/i,
  /\.(?:py|pyc|db|sqlite3?|ya?ml|env|log|ini|toml)\b(?::\d+)?/i,
  /(?:^|[\s"'(=:])\/(?:home|root|tmp|var|usr|opt|etc|srv|app|workspace|Users|private|data)\//,
  /\b[A-Za-z]:\\[^\s]/,
  /\b(?:https?|wss?|ftp|file|sqlite|postgres(?:ql)?|mysql|redis):\/\/\S+/i,
  /\b(?:bearer|authorization)\b\s*[:=]?\s*[A-Za-z0-9._~+/-]{8,}/i,
  /\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token)\s*[:=]\s*\S+/i,
  /\b(?:sk|ak|ghp|gho|xox[abp])-[A-Za-z0-9_-]{8,}/,
  /\b[A-Za-z0-9_]{40,}\b/,
  /\b[A-Z][A-Z0-9]*_(?:API_KEY|KEY|TOKEN|SECRET|PASSWORD|URL)\b/,
  /\b(?:sqlite3|sqlalchemy|psycopg2?|pymysql)\.|\b(?:OperationalError|ProgrammingError|IntegrityError|KeyError|TypeError|ValueError|AttributeError|RuntimeError|Exception)\b/,
  /constraint failed|no such (?:table|column)|database is locked/i,
  /<\/?(?:html|body|head|title)\b/i,
];

/** Convert an API error detail to a safe, user-facing message. */
export function clientErrorMessage(status, rawDetail) {
  const detail = typeof rawDetail === "string" ? rawDetail.trim() : "";
  const generic = status >= 500
    ? "服务暂时不可用，请稍后重试"
    : "操作失败，内部诊断信息已记录，请检查配置或联系管理员";
  if (!detail) return status >= 500 ? generic : "请求失败，请稍后重试";
  if (INTERNAL_DETAIL_PATTERNS.some((pattern) => pattern.test(detail))) return generic;
  return detail;
}
