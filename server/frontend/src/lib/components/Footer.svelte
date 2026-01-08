<script lang="ts">
  import { versionInfo } from '../stores/settings';

  let buildInfo = $derived(
    $versionInfo
      ? `Build: ${$versionInfo.git_commit} (${$versionInfo.build_time})`
      : 'Build: loading...'
  );
</script>

<footer class="app-footer">
  <span>{buildInfo}</span>
</footer>

<style>
  .app-footer {
    text-align: center;
    padding: 24px;
    border-top: 1px solid var(--color-border);
    margin-top: auto;
    color: var(--color-text-dim);
    font-size: 11px;
  }
</style>
