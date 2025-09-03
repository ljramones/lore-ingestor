# scripts/temporal_smoke.py
import asyncio, os, time
from temporalio.client import Client

async def main():
    target = os.getenv("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "ingest-queue")

    client = await Client.connect(target, namespace=namespace)
    wf_id = f"post-ingest-smoke-{int(time.time())}"
    result = await client.execute_workflow(
        "PostIngestWorkflow",                         # workflow name from your worker
        id=wf_id,
        task_queue=task_queue,
        input={"work_id": "<PUT_A_REAL_WORK_ID_HERE>", "db_path": os.getenv("DB_PATH", "./tropes.db")},
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
