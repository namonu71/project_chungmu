import type { Metadata } from 'next';
export const metadata: Metadata = {title:'충무 | 주문 운영 대시보드',description:'주문별 진행과 출고 택배 송장 결과물을 확인하는 충무 업무 자동화',robots:{index:false,follow:false},icons:{icon:'/favicon.svg'}};
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>) { return <html lang="ko"><body>{children}</body></html>; }
