import { useEffect, useState } from "react";

/** The value, but only after it has stopped changing for `ms` (so typing does not fire a request per key). */
export function useDebounced<T>(value: T, ms = 400): T {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setShown(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return shown;
}
