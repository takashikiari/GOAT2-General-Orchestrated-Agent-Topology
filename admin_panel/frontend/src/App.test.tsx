import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('shows the Dashboard tab active by default', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('panel-dashboard')).toBeInTheDocument()
  })

  it('switches the active tab on click', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Logs' }))
    expect(screen.getByRole('button', { name: 'Logs' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('panel-logs')).toBeInTheDocument()
  })
})
