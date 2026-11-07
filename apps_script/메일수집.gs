/**
 * 충무 자동 수집 — Gmail 주문 메일을 Google Drive로 옮겨 담는 스크립트 (Google Apps Script)
 *
 * 왜 필요한가: Claude 클라우드 예약 작업은 Gmail을 직접 읽지 못한다. 이 스크립트가 Google 서버에서
 * 15분마다 돌며(PC가 꺼져 있어도 동작) 주문 메일 원문을 Drive '충무자동수집/메일원문'에 저장하면,
 * 클라우드 작업이 그 파일을 읽어 주문을 추출한다.
 *
 * 설치(주문 메일을 받는 Google 계정으로 한 번만):
 *   1) https://script.google.com → 새 프로젝트 → 이 파일 내용을 전부 붙여 넣고 저장
 *   2) 위쪽 함수 선택에서 setup 을 고르고 ▶ 실행 → 권한 요청을 허용(Gmail 읽기, Drive 파일)
 *   3) 끝. 실행 기록은 왼쪽 '실행' 메뉴에서 볼 수 있다. 멈추려면 stop 을 실행한다.
 *
 * 하는 일: 메일을 읽기만 한다. 보내기·삭제·라벨 변경은 하지 않는다.
 * 검색 조건은 대시보드에서 저장한 '충무자동수집_설정.txt'의 gmail.query 를 따른다(enabled=false면 쉰다).
 */
const ROOT_NAME = '충무자동수집';
const SETTINGS_NAME = '충무자동수집_설정.txt';
const INBOX_NAME = '메일원문';
const LOOKBACK = 'newer_than:3d';
const MAX_BODY = 50000;
const MAX_ATTACHMENT = 5 * 1024 * 1024;
const TEXT_ATTACHMENT = /\.(csv|tsv|txt)$/i;
const FILE_ATTACHMENT = /\.(xlsx|xls|pdf|png|jpe?g)$/i;

function setup() {
  stop();
  ScriptApp.newTrigger('collect').timeBased().everyMinutes(15).create();
  collect();
  Logger.log('설치 완료: 15분마다 주문 메일을 Drive에 저장합니다.');
}

function stop() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
}

function childFolder_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function readSettings_(root) {
  const files = root.getFilesByName(SETTINGS_NAME);
  if (!files.hasNext()) return null;
  try { return JSON.parse(files.next().getBlob().getDataAsString('UTF-8')); }
  catch (e) { Logger.log('설정 파일을 읽지 못했습니다: ' + e); return null; }
}

function collect() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const roots = DriveApp.getRootFolder().getFoldersByName(ROOT_NAME);
    if (!roots.hasNext()) { Logger.log('내 드라이브에 ' + ROOT_NAME + ' 폴더가 없습니다.'); return; }
    const root = roots.next();
    const settings = readSettings_(root);
    if (!settings || !settings.enabled || !settings.gmail || !settings.gmail.query) { Logger.log('자동 수집이 꺼져 있거나 메일 검색 조건이 없습니다.'); return; }
    const inbox = childFolder_(root, INBOX_NAME);
    const props = PropertiesService.getScriptProperties();
    const seen = JSON.parse(props.getProperty('seen') || '[]');
    const seenSet = new Set(seen);
    const tz = Session.getScriptTimeZone() || 'Asia/Seoul';
    let saved = 0;
    GmailApp.search(settings.gmail.query + ' ' + LOOKBACK, 0, 50).forEach(thread => {
      thread.getMessages().forEach(msg => {
        const id = msg.getId();
        if (seenSet.has(id)) return;
        const stamp = Utilities.formatDate(msg.getDate(), 'Asia/Seoul', "yyyy-MM-dd'T'HH:mm:ss");
        const data = { id: id, thread_id: thread.getId(), subject: msg.getSubject(), from: msg.getFrom(), date: stamp,
                       body: msg.getPlainBody().slice(0, MAX_BODY), attachments: [] };
        msg.getAttachments({ includeInlineImages: false }).forEach(att => {
          const name = att.getName();
          if (att.getSize() > MAX_ATTACHMENT) { data.attachments.push({ name: name, skipped: '5MB 초과' }); return; }
          if (TEXT_ATTACHMENT.test(name)) data.attachments.push({ name: name, text: att.getDataAsString('UTF-8').slice(0, MAX_BODY) });
          else if (FILE_ATTACHMENT.test(name)) {
            const file = inbox.createFile(att.copyBlob()).setName('att_' + id + '_' + name);
            data.attachments.push({ name: name, fileId: file.getId() });
          } else data.attachments.push({ name: name, skipped: '지원하지 않는 형식' });
        });
        const fileName = 'mail_' + stamp.replace(/[-:]/g, '').replace('T', '_') + '_' + id + '.txt';
        inbox.createFile(fileName, JSON.stringify(data), MimeType.PLAIN_TEXT);
        seen.push(id); seenSet.add(id); saved++;
      });
    });
    props.setProperty('seen', JSON.stringify(seen.slice(-3000)));
    Logger.log('저장한 메일: ' + saved + '건 (' + Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd HH:mm') + ')');
  } finally {
    lock.releaseLock();
  }
}
