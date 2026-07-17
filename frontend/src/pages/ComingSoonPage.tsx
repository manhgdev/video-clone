type Props = {
  title: string
  description?: string
}

export default function ComingSoonPage({ title, description }: Props) {
  return (
    <div
      style={{
        minHeight: 'calc(100vh - 56px)',
        display: 'grid',
        placeItems: 'center',
        padding: 32,
      }}
    >
      <div
        style={{
          maxWidth: 420,
          textAlign: 'center',
          border: '1px solid var(--border, #e2e8f0)',
          borderRadius: 16,
          padding: '36px 28px',
          background: 'var(--card, #fff)',
        }}
      >
        <div style={{ fontSize: 36, marginBottom: 8 }}>🚀</div>
        <h1 style={{ margin: '0 0 8px', fontSize: '1.25rem' }}>{title}</h1>
        <p style={{ margin: 0, color: 'var(--muted-foreground, #64748b)' }}>
          {description || 'Trang này đang được phát triển.'}
        </p>
        <p
          style={{
            margin: '14px 0 0',
            fontWeight: 700,
            color: '#2563eb',
          }}
        >
          Sắp ra mắt…
        </p>
      </div>
    </div>
  )
}
