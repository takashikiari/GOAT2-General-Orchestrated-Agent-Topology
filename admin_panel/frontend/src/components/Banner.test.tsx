import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Banner } from './Banner'

describe('Banner', () => {
  it('renders an error message', () => {
    render(<Banner kind="error" message="Letta unavailable" />)
    expect(screen.getByText('Letta unavailable')).toBeInTheDocument()
  })

  it('renders a warning message', () => {
    render(<Banner kind="warning" message="Redis unavailable" />)
    expect(screen.getByText('Redis unavailable')).toBeInTheDocument()
  })
})
