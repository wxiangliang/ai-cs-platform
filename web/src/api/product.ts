/**
 * 商品管理 API（对齐 app/api/routes/product.py，admin scope）。
 */
import { get, post } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export interface ProductItem {
  id: string;
  product_code: string | null;
  name: string;
  category: string | null;
  price_cents: number | null;
  stock: number | null;
  status: string;
  description: string | null;
  attrs: Record<string, unknown> | null;
  updated_at: string;
}

export function listProducts(opts: {
  keyword?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: ProductItem[]; has_more: boolean }> {
  const params = new URLSearchParams({
    tenant_id: useAuthStore().tenantId,
    limit: String(opts.limit ?? 50),
    offset: String(opts.offset ?? 0),
  });
  if (opts.keyword) params.set("keyword", opts.keyword);
  return get(`/api/product/items?${params.toString()}`);
}

export function upsertProduct(payload: {
  name: string;
  product_code?: string;
  category?: string;
  price_cents?: number;
  stock?: number;
  description?: string;
}): Promise<unknown> {
  return post("/api/product/items", { tenant_id: useAuthStore().tenantId, ...payload });
}
