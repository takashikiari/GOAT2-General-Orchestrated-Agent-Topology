import { useEffect, useState } from 'react'

const MOBILE_BREAKPOINT_PX = 768

/**
 * True below 768px. Backed by matchMedia so it updates on rotation/resize,
 * not just on mount. Renders exactly one nav layout at a time — never both
 * sidebar and drawer in the DOM together, which would make every nav button
 * ambiguous to query by accessible name in tests.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => window.innerWidth < MOBILE_BREAKPOINT_PX,
  )

  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT_PX - 1}px)`)
    const onChange = () => setIsMobile(query.matches)
    onChange()
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  return isMobile
}
