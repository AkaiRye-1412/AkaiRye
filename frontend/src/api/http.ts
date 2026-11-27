/** REST 客户端：统一错误包络处理（api-rules：{error: {code, message}}）。 */

export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

/** 响应体不是 JSON 时的兜底提示：多半是后端没挂上路由、由前端静态兜底返回了 index.html。 */
function nonJsonMessage(status: number, body: string): string {
  if (body.trimStart().startsWith('<')) {
    return `接口未返回数据（HTTP ${status}），后端可能未重启或该路由未注册`
  }
  return `接口返回了非 JSON 内容（HTTP ${status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError('network', 0, '无法连接本地后端服务，请确认服务是否在运行')
  }

  if (!resp.ok) {
    let code = 'unknown'
    let message = resp.statusText || `请求失败（HTTP ${resp.status}）`
    try {
      const body = await resp.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
    } catch {
      /* 非 JSON 错误体：保留状态码文案 */
    }
    throw new ApiError(code, resp.status, message)
  }

  if (resp.status === 204) return undefined as T

  const ct = resp.headers.get('content-type') || ''
  if (!ct.includes('json')) {
    const text = await resp.text()
    throw new ApiError('not_json', resp.status, nonJsonMessage(resp.status, text))
  }

  try {
    return (await resp.json()) as T
  } catch {
    throw new ApiError('bad_json', resp.status, '接口响应解析失败，请重试')
  }
}

export const http = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
