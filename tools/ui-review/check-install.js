// Real Steam controls, fixture RPC only: never downloads a game.
(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const checks = [];
  const doc = () => document.getElementById('nfd-layout-preview').contentDocument;
  const button = label => [...doc().querySelectorAll('button')].find(b => b.textContent === label);
  const calls = kind => __nfdPreviewCalls.filter(c => c[0] === kind);
  const check = (name, pass) => { checks.push({ name, pass }); if (!pass) throw Error(JSON.stringify(checks)); };
  async function show(name, error = '') {
    window.__nfdPreviewCalls = [];
    window.__nfdPreviewDownloadError = error;
    __nfdPreviewShow('install-' + name);
    await wait(700);
    check(name + ': rendered', !!button('安装') && !doc().querySelector('[data-preview-error]'));
  }
  await show('click');
  const install = button('安装');
  install.click(); install.click();
  await wait(250);
  check('click starts exactly one download', calls('download').length === 1 && calls('started').length === 1);
  check('selected file is sent', calls('download')[0][1].file_name === 'fixture.zip');
  await wait(450);
  check('success closes details', calls('close').length === 1);
  await show('gamepad');
  const element = button('安装');
  let fiber = element[Object.keys(element).find(key => key.startsWith('__reactFiber'))];
  while (fiber && typeof fiber.memoizedProps?.onOKButton !== 'function') fiber = fiber.return;
  check('gamepad confirm callback is bound', !!fiber);
  fiber.memoizedProps.onOKButton(); element.click();
  await wait(250);
  check('confirm callback plus click starts exactly one download', calls('download').length === 1 && calls('started').length === 1);
  await wait(450);
  await show('failure', '测试：登录已过期，请重新登录');
  button('安装').click(); await wait(250);
  check('failure stays visible and allows retry', doc().body.innerText.includes('测试：登录已过期') && !button('安装').disabled && calls('close').length === 0);
  window.__nfdPreviewDownloadError = '';
  button('安装').click(); await wait(250);
  check('retry creates one task', calls('download').length === 2 && calls('started').length === 1);
  await wait(450);
  await show('cancel');
  button('取消').click(); await wait(450);
  check('cancel works without a download', calls('close').length === 1 && calls('download').length === 0);
  await show('more');
  button('···').click(); await wait(100);
  check('more opens its menu', calls('menu').length === 1);
  return { environment: 'Steam native controls with fixture RPC; confirm callback invoked directly; not physical gamepad', checks };
})()
