import { addEventListener, removeEventListener } from "@decky/api";
import { useEffect, useState } from "react";
export const CATALOG_CHANGED = "newfreedeck-catalog-changed";
export function startCatalogUpdates() {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const listener = addEventListener<[string]>("catalog_updated", () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => window.dispatchEvent(new Event(CATALOG_CHANGED)), 350);
  });
  return () => { if (timer) clearTimeout(timer); removeEventListener("catalog_updated", listener); };
}
export function useCatalogRevision() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const update = () => setRevision(value => value + 1);
    window.addEventListener(CATALOG_CHANGED, update);
    return () => window.removeEventListener(CATALOG_CHANGED, update);
  }, []);
  return revision;
}
