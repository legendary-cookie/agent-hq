"""Build a follow-up from the launch settings captured for a run."""


def resume_launch(launcher, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
    return launcher.build_resume(req, profile, workdir, run_id, session_id, text, env_base, pass_names)
