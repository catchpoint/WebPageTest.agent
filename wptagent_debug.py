import debugpy
import os
# first try to import with the original name
try:
   from wptagent import main
except ImportError:
    # then import as the contianer require
    from wptagent_starter import main

if __name__ == '__main__':
    print("Waiting for debug")
    debugpy.listen(("0.0.0.0", 50000))
    debugpy.wait_for_client()
    main()
    # Force a hard exit so unclean threads can't hang the agent
    os._exit(0)
