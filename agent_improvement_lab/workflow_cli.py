"""One CLI for saved replay and explicitly approved model experiments."""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'experiment':
        from .experiment import main as execution_main
        execution_main(sys.argv[2:])
    else:
        from .workflow import main as replay_main
        replay_main()


if __name__ == '__main__':
    main()
