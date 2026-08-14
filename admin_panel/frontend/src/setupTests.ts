import { expect, afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom'

// jsdom has no matchMedia implementation at all. Default to "not mobile"
// (matches: false) so every existing test — none of which are aware of
// useIsMobile — keeps exercising the desktop layout unchanged. A test that
// needs the mobile/drawer path overrides window.matchMedia locally.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// Fail loudly on any unmocked network call instead of silently "succeeding"
// by luck (e.g. Node's native fetch throwing on a relative URL). Tests that
// need real request/response behavior should stub `fetch` explicitly via
// vi.stubGlobal('fetch', ...) inside the test; every other test should mock
// client.apiGet instead. Re-stubbed before every test (not just once at
// module load) because afterEach's vi.unstubAllGlobals() below reverts
// `fetch` to its real, unstubbed value after each test — including after a
// test that installed its own fetch stub — so later tests in the same file
// would otherwise fall through to the real fetch instead of this guard.
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => {
      throw new Error('Unmocked fetch call in test — stub it with vi.stubGlobal or mock client.apiGet')
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
