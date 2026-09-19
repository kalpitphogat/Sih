/**
 * Thin fetch wrapper. Every number the UI renders comes through here from the
 * backend; the frontend holds no hardcoded results.
 */

const BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json())?.detail ?? detail
    } catch {
      /* body was not JSON; keep the status text */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}
