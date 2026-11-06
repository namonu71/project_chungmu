import { shell } from '../lib/shell';
export const dynamic = 'force-dynamic';
export default function Home() { return <><meta name="local-token" content="hosted-same-origin"/><link rel="stylesheet" href="/styles.css"/><div dangerouslySetInnerHTML={{__html:shell}}/><script src="/app.js" defer/><script src="/extract.js" defer/></>; }
