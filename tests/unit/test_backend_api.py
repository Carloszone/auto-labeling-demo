import asyncio
import json
import os

import httpx

# API tests must never initialize JobService against the operator's live Demo
# workspace because service startup intentionally removes stale temporary jobs.
os.environ["AUTO_LABEL_WORKSPACE_ROOT"] = "/tmp/auto-labeling-demo-tests"
os.environ["AUTO_LABEL_LOG_PATH"] = "/tmp/auto-labeling-demo-tests/test.log"

from backend.main import app


def run_scenario(scenario):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await scenario(client)

    return asyncio.run(send())


async def _delete_current_if_present(client: httpx.AsyncClient) -> None:
    current = await client.get("/api/v1/jobs/current")
    if current.status_code == 200:
        await client.delete(f"/api/v1/jobs/{current.json()['job_id']}")


def test_health_and_config_are_available():
    async def scenario(client: httpx.AsyncClient):
        health = await client.get("/api/v1/health")
        config = await client.get("/api/v1/config")
        page = await client.get("/")
        return health, config, page

    health, config, page = run_scenario(scenario)
    assert health.status_code == 200
    assert health.json()["h264_encoder_available"] is True
    assert config.status_code == 200
    assert config.json()["pipeline_defaults"]["data_check_config"]["image_detection"]["resize_length"] == 860
    assert page.status_code == 200
    assert "自动标注 Demo" in page.text


def test_upload_creates_current_job_and_delete_clears_it():
    async def scenario(client: httpx.AsyncClient):
        await _delete_current_if_present(client)
        with open("tests/train_data_1.json", "rb") as robot:
            response = await client.post(
                "/api/v1/jobs",
                files={
                    "mcap": ("sample.mcap", b"not parsed until run", "application/octet-stream"),
                    "robot_config": ("robot.json", robot, "application/json"),
                },
            )
        body = response.json()
        current = await client.get("/api/v1/jobs/current")
        deleted = await client.delete(f"/api/v1/jobs/{body['job_id']}")
        missing = await client.get("/api/v1/jobs/current")
        return response, body, current, deleted, missing

    response, body, current, deleted, missing = run_scenario(scenario)
    assert response.status_code == 201
    assert body["status"] == "ready_to_run"
    assert body["job_id"].startswith("job")
    assert body["available_camera_topics"]
    assert current.json()["job_id"] == body["job_id"]
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_upload_rejects_wrong_extension():
    async def scenario(client: httpx.AsyncClient):
        await _delete_current_if_present(client)
        config = json.dumps({"main_time_topic": "/camera", "cameras": []}).encode()
        return await client.post(
            "/api/v1/jobs",
            files={
                "mcap": ("sample.txt", b"bad", "text/plain"),
                "robot_config": ("robot.json", config, "application/json"),
            },
        )

    response = run_scenario(scenario)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MCAP"


def test_run_rejects_out_of_range_page_configuration():
    async def scenario(client: httpx.AsyncClient):
        return await client.post(
            "/api/v1/jobs/missing/run",
            json={
                "data_check_config": {"image_detection": {"resize_length": 12}},
                "event_labeling_config": {"sampling": {"params": {"fixed_frame_len": 21}}},
            },
        )

    response = run_scenario(scenario)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIG_VALIDATION_FAILED"
