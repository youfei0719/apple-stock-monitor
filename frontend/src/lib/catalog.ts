import type { Product, Task } from './api';

export function configurationLabel(product: Product): string {
  return [...new Set(Object.values(product.attributes ?? {}).filter(Boolean))].join(' · ')
    || [product.capacity, product.color].filter(Boolean).join(' · ')
    || '标准配置';
}

export function productTitle(product: Product): string {
  return `${product.name} · ${configurationLabel(product)}`;
}

/** Choose actual rows; never manufacture capacity × color × network combinations. */
export function selectCatalogParts(products: Product[], filters: Record<string, Set<string>>): string[] {
  const dimensions = Object.keys(filters);
  if (dimensions.some((key) => filters[key].size === 0)) return [];
  return [...new Set(products.filter((p) => p.part_number && p.monitorable !== false && dimensions.every(
    (key) => filters[key].has(key === '存储容量' ? p.capacity : key === '颜色' ? p.color : p.attributes?.[key] ?? ''),
  )).map((p) => p.part_number))];
}

/** Older tasks store short names; newer tasks already include all configuration axes. */
export function taskProductTitle(task: Pick<Task, 'product_name' | 'capacity' | 'color'>): string {
  return [task.product_name, ...[task.capacity, task.color].filter(
    (detail): detail is string => Boolean(detail && !task.product_name.includes(detail)),
  )].join(' · ');
}
